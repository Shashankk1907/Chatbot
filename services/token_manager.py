# services/token_manager.py
# TokenManager — enforces the 32K context window with a structured token budget.
#
# Budget breakdown (32,768 total):
#   70% usable = 22,937 tokens
#   System prompt:   500 (fixed)
#   Output buffer: 4,000 (fixed, reserved for LLM response)
#   Safety margin:   500 (fixed, prevents edge-case overflows)
#   Summary:       3,000 (soft cap, trimmed if needed)
#   User message:  variable (never trimmed — reject if too large)
#   Recent memory: remaining (trimmed first when over budget)

import logging
from utils.token_counter import count_tokens, count_messages_tokens
from db.redis_helpers import (
    track_tokens_user,
    track_tokens_chat,
    get_user_daily_tokens,
)
from db.mongo_helpers import increment_user_token_stats

logger = logging.getLogger(__name__)

# ── Budget constants ──────────────────────────────────────────────────────
CONTEXT_WINDOW = 32_768
USABLE_RATIO = 0.70
USABLE_WINDOW = int(CONTEXT_WINDOW * USABLE_RATIO)   # 22,937

SYSTEM_PROMPT_BUDGET = 500
OUTPUT_BUFFER = 4_000
SAFETY_MARGIN = 500
SUMMARY_BUDGET = 3_000

# Fixed overhead = system + output + safety = 5,000
FIXED_OVERHEAD = SYSTEM_PROMPT_BUDGET + OUTPUT_BUFFER + SAFETY_MARGIN

# Maximum tokens available for (summary + recent memory + user message)
DYNAMIC_BUDGET = USABLE_WINDOW - FIXED_OVERHEAD   # ~17,937

# ── Per-user / per-chat limits ────────────────────────────────────────────
USER_DAILY_TOKEN_LIMIT = 100_000    # 100K tokens/day for free tier
CHAT_LIFETIME_TOKEN_LIMIT = 500_000 # 500K tokens per chat


class TokenManager:
    """
    Manages token budgets and usage tracking.

    Two responsibilities:
      1. Budget calculation: allocate tokens to each payload segment
      2. Usage tracking: record consumption to Redis + Mongo
    """

    def __init__(self, db, redis):
        self.db = db
        self.redis = redis

    # ── Budget Calculation ────────────────────────────────────────────

    def calculate_budget(
        self,
        system_prompt: str,
        summary: str | None,
        messages: list[dict],
        user_message: str,
    ) -> dict:
        """
        Calculate the token budget allocation for an LLM request.

        Allocation priority (highest to lowest):
          1. Non-negotiable: system + user + output buffer + safety
          2. Summary (up to 3K, truncated if needed)
          3. Recent memory (trimmed first — oldest messages dropped)

        Returns a dict with:
          - system_tokens, summary_tokens, memory_tokens, user_tokens
          - output_budget: reserved for LLM response
          - total_input: total input tokens
          - fits: bool — whether the request fits in the window
          - trimmed_messages, trimmed_summary
        """
        system_tokens = count_tokens(system_prompt)
        user_tokens = count_tokens(user_message)

        # Fixed: system + user + output + safety (these cannot be trimmed)
        non_negotiable = system_tokens + user_tokens + OUTPUT_BUFFER + SAFETY_MARGIN

        if non_negotiable > USABLE_WINDOW:
            # User message alone exceeds budget — reject
            return {
                "fits": False,
                "reason": (
                    f"Message too long ({user_tokens} tokens). "
                    f"Maximum ~{USABLE_WINDOW - system_tokens - OUTPUT_BUFFER - SAFETY_MARGIN} tokens."
                ),
                "system_tokens": system_tokens,
                "user_tokens": user_tokens,
                "output_budget": OUTPUT_BUFFER,
                "total_input": non_negotiable,
                "trimmed_messages": [],
                "trimmed_summary": None,
            }

        remaining = USABLE_WINDOW - non_negotiable

        # Allocate summary (up to SUMMARY_BUDGET or available)
        trimmed_summary = summary
        summary_tokens = 0
        if summary:
            summary_tokens = count_tokens(summary)
            max_summary = min(SUMMARY_BUDGET, remaining)
            if summary_tokens > max_summary:
                # Truncate summary to fit
                trimmed_summary = self._truncate_to_tokens(summary, max_summary)
                summary_tokens = count_tokens(trimmed_summary)
            remaining -= summary_tokens

        # Allocate recent messages (trim oldest first)
        trimmed_messages = self.trim_context(messages, remaining)
        memory_tokens = count_messages_tokens(trimmed_messages)

        total_input = (
            system_tokens + summary_tokens
            + memory_tokens + user_tokens
        )

        return {
            "fits": True,
            "system_tokens": system_tokens,
            "summary_tokens": summary_tokens,
            "memory_tokens": memory_tokens,
            "user_tokens": user_tokens,
            "output_budget": OUTPUT_BUFFER,
            "total_input": total_input,
            "trimmed_messages": trimmed_messages,
            "trimmed_summary": trimmed_summary,
        }

    def trim_context(self, messages: list[dict], available_tokens: int) -> list[dict]:
        """
        Trim messages to fit within available_tokens.
        Drops OLDEST messages first (keeps most recent context).

        Returns a new list (does not mutate input).
        """
        if not messages:
            return []

        # Count from newest to oldest, accumulate until budget runs out
        result = []
        used = 0
        for msg in reversed(messages):
            msg_tokens = count_tokens(msg.get("content", "")) + 4  # overhead
            if used + msg_tokens > available_tokens:
                break
            result.append(msg)
            used += msg_tokens

        result.reverse()  # restore chronological order
        return result

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to approximately max_tokens."""
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = enc.decode(tokens[:max_tokens])
        return truncated

    # ── Usage Tracking ────────────────────────────────────────────────

    async def check_user_limits(self, user_id: str) -> dict:
        """
        Check if a user has exceeded their daily token limit.

        Returns: {"allowed": True} or {"allowed": False, "reason": "..."}
        """
        daily = await get_user_daily_tokens(self.redis, user_id)
        if daily >= USER_DAILY_TOKEN_LIMIT:
            return {
                "allowed": False,
                "reason": f"Daily token limit reached ({daily}/{USER_DAILY_TOKEN_LIMIT}).",
            }
        return {"allowed": True, "remaining": USER_DAILY_TOKEN_LIMIT - daily}

    async def check_chat_limit(self, chat_id: str) -> dict:
        """Check if a chat has exceeded its lifetime token limit."""
        from db.mongo_helpers import get_chat_by_id

        chat = await get_chat_by_id(self.db, chat_id)
        if not chat:
            return {"allowed": True}

        total = chat.get("totalTokens", 0)
        if total >= CHAT_LIFETIME_TOKEN_LIMIT:
            return {
                "allowed": False,
                "reason": f"Chat token limit reached ({total}/{CHAT_LIFETIME_TOKEN_LIMIT}).",
            }
        return {"allowed": True, "remaining": CHAT_LIFETIME_TOKEN_LIMIT - total}

    async def record_usage(self, user_id: str, chat_id: str, tokens: int):
        """
        Record token usage to both Redis (real-time) and Mongo (persistent).
        Mongo updates happen inside insert_message (already atomic).
        This method handles the Redis side.
        """
        try:
            await track_tokens_user(self.redis, user_id, tokens)
            await track_tokens_chat(self.redis, chat_id, tokens)
            # Sync to Mongo in real-time
            await increment_user_token_stats(self.db, user_id, tokens)
        except Exception as e:
            logger.warning(f"Failed to track tokens: {e}")
            # Non-fatal — Mongo is the source of truth
