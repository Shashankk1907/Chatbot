# services/model_router.py
#
# ModelRouter — task-based LLM routing with retry, fallback, and logging.
#
# Routes requests to the appropriate model based on task type:
#   - "chat"      → primary conversational model
#   - "summary"   → cheaper/deterministic model (low temperature)
#   - "embedding" → sentence-transformers (handled by VectorManager)
#
# Features:
#   - Configurable providers per task
#   - Timeout handling per task
#   - Retry with backoff (max 2 attempts)
#   - Fallback model if primary fails
#   - Centralized logging of model, latency, and tokens

import asyncio
import logging
import time
from utils.token_counter import count_tokens, count_messages_tokens

logger = logging.getLogger(__name__)

# ── Default configuration ─────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "chat": {
        "provider": "primary",       # uses call_llm_async
        "temperature": 0.7,
        "max_tokens": 4000,
        "timeout": 120,              # seconds
        "max_retries": 2,
        "backoff_base": 1,           # seconds: 1 → 2
    },
    "summary": {
        "provider": "primary",       # same provider, different params
        "temperature": 0.3,          # low temp for deterministic output
        "max_tokens": 2000,
        "timeout": 60,
        "max_retries": 2,
        "backoff_base": 1,
    },
}

# ── Fallback configuration ────────────────────────────────────────────────
# If the primary model fails, fall back to this config.
# Set to None to disable fallback.

FALLBACK_CONFIG = {
    "chat": {
        "provider": "primary",
        "temperature": 0.5,          # more conservative
        "max_tokens": 2000,          # lower output cap
        "timeout": 60,               # shorter timeout
    },
    "summary": {
        "provider": "primary",
        "temperature": 0.2,
        "max_tokens": 1000,
        "timeout": 30,
    },
}


class ModelRouter:
    """
    Task-based model routing with retry, fallback, and centralized logging.

    Usage:
        router = ModelRouter()
        result = await router.generate(task="chat", messages=[...])
    """

    def __init__(self, config: dict | None = None, fallback_config: dict | None = None):
        self.config = config or DEFAULT_CONFIG
        self.fallback_config = fallback_config or FALLBACK_CONFIG
        self._llm_fn = None  # lazy-loaded

    def _get_llm_fn(self):
        """Lazy-load the LLM function to avoid circular imports."""
        if self._llm_fn is None:
            from utils.gemini_api import call_llm_async
            self._llm_fn = call_llm_async
        return self._llm_fn

    async def generate(
        self,
        task: str,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        """
        Route an LLM request based on task type.

        Args:
            task: One of "chat", "summary"
            messages: OpenAI-style messages list
            **kwargs: Override any config parameter

        Returns:
            {"role": "assistant", "content": "...", "meta": {...}}
            where meta includes model, latency_ms, tokens_in, tokens_out

        Raises:
            RuntimeError: If all attempts (including fallback) fail
        """
        if task not in self.config:
            raise ValueError(f"Unknown task type: {task}. Expected: {list(self.config.keys())}")

        task_config = {**self.config[task], **kwargs}

        # Try primary model
        result = await self._try_with_retries(task, task_config, messages, is_fallback=False)
        if result is not None:
            return result

        # Try fallback model (if configured)
        if task in self.fallback_config:
            fallback = {**self.fallback_config[task], **kwargs}
            logger.warning(f"[Router] Primary failed for task={task}, trying fallback...")
            result = await self._try_with_retries(task, fallback, messages, is_fallback=True)
            if result is not None:
                return result

        raise RuntimeError(
            f"All model attempts failed for task={task} "
            f"(primary + fallback exhausted)"
        )

    async def _try_with_retries(
        self,
        task: str,
        config: dict,
        messages: list[dict],
        is_fallback: bool = False,
    ) -> dict | None:
        """
        Try calling the model with retries and exponential backoff.
        Returns the result dict or None if all retries are exhausted.
        """
        max_retries = config.get("max_retries", 2)
        backoff_base = config.get("backoff_base", 1)
        timeout = config.get("timeout", 120)
        temperature = config.get("temperature", 0.7)
        max_tokens = config.get("max_tokens", 4000)

        model_label = f"{'fallback' if is_fallback else 'primary'}/{task}"

        for attempt in range(max_retries):
            start_time = time.monotonic()
            try:
                llm_fn = self._get_llm_fn()

                # Wrap in timeout
                result = await asyncio.wait_for(
                    llm_fn(messages, temperature, max_tokens),
                    timeout=timeout,
                )

                elapsed_ms = (time.monotonic() - start_time) * 1000
                tokens_in = count_messages_tokens(messages)
                tokens_out = count_tokens(result.get("content", ""))

                # Centralized logging
                logger.info(
                    f"[Router] {model_label} | "
                    f"attempt={attempt + 1}/{max_retries} | "
                    f"latency={elapsed_ms:.0f}ms | "
                    f"tokens_in={tokens_in} tokens_out={tokens_out}"
                )

                # Attach metadata
                result["meta"] = {
                    "model": model_label,
                    "latency_ms": round(elapsed_ms),
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "attempt": attempt + 1,
                    "is_fallback": is_fallback,
                }

                return result

            except asyncio.TimeoutError:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                logger.warning(
                    f"[Router] {model_label} timeout after {elapsed_ms:.0f}ms "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
            except RuntimeError as e:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                error_str = str(e).lower()
                is_retryable = (
                    "429" in error_str
                    or "rate" in error_str
                    or "timeout" in error_str
                    or "unavailable" in error_str
                )

                if is_retryable:
                    logger.warning(
                        f"[Router] {model_label} retryable error "
                        f"(attempt {attempt + 1}/{max_retries}): {e}"
                    )
                else:
                    logger.error(
                        f"[Router] {model_label} non-retryable error: {e}"
                    )
                    return None  # don't retry non-retryable errors

            except Exception as e:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                logger.error(
                    f"[Router] {model_label} unexpected error "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                return None  # don't retry unexpected errors

            # Backoff before next attempt
            if attempt < max_retries - 1:
                wait = backoff_base * (2 ** attempt)
                logger.info(f"[Router] Retrying {model_label} in {wait}s...")
                await asyncio.sleep(wait)

        logger.error(
            f"[Router] {model_label} exhausted all {max_retries} retries"
        )
        return None
