# services/chat_orchestrator.py
#
# ChatOrchestrator — end-to-end request lifecycle handler.
#
# Implements the full 11-step flow:
#   1. Acquire chat lock
#   2. Validate rate limits
#   3. Validate token limits
#   4. Load memory (Redis → Mongo fallback)
#   5. Build token-aware LLM payload
#   6. Call LLM (with retry + exponential backoff)
#   7. Store assistant message
#   8. Increment token counters (atomic)
#   9. Push to Redis sliding window
#   10. Trigger summarization if needed
#   11. Release lock
#
# Concurrency safety:
#   - Redis SETNX lock prevents double processing per chat
#   - Atomic Mongo $inc prevents double counting
#   - Lock release in finally block prevents deadlocks
#   - Summarization runs under the same lock scope
#
# Idempotency:
#   - Each message gets a unique Mongo _id on insert
#   - Lock prevents duplicate inserts for the same chat
#   - Rate limit counters use INCR (idempotent under retries with same key)

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from utils.token_counter import count_tokens
from utils.gemini_api import call_llm_async
from db.redis_helpers import (
    acquire_chat_lock, release_chat_lock,
    enqueue_memory_extraction_job
)
from db.mongo_helpers import (
    update_chat_title, get_chat_by_id
)
from services.memory_manager import MemoryManager
from services.token_manager import TokenManager
from services.rate_limiter import RateLimiter
from services.model_router import ModelRouter
from services.personas import PERSONAS
from services.user_memory_extractor import UserMemoryExtractor

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are a helpful, accurate, and concise AI assistant. "
    "Use the provided conversation history and summary to maintain context. "
    "Answer the user's question based on the conversation so far. "
    "If you don't know something, say so honestly."
)

# ── Retry config ──────────────────────────────────────────────────────────
MAX_RETRIES = 4
BACKOFF_BASE = 1  # seconds: 1 → 2 → 4 → 8


class ChatOrchestrator:
    """
    Production-grade request handler for chat messages.

    Usage:
        orchestrator = ChatOrchestrator(db, redis)
        result = await orchestrator.handle_message(user_id, chat_id, "Hello!")
    """

    def __init__(self, db, redis):
        self.db = db
        self.redis = redis
        self.memory = MemoryManager(db, redis)
        self.tokens = TokenManager(db, redis)
        self.rate_limiter = RateLimiter(redis)
        self.router = ModelRouter()
        self.memory_extractor = UserMemoryExtractor(db, redis)

    async def handle_message(
        self, user_id: str, chat_id: str, text: str, persona: str = "default", attachments: list | None = None
    ) -> dict:
        """
        Full 11-step request lifecycle.
        """
        lock_released = False

        # ── Step 1: Acquire chat lock ─────────────────────────────────
        locked = await acquire_chat_lock(self.redis, chat_id)
        if not locked:
            return {
                "status": "error",
                "code": 423,
                "reason": "Chat is busy — another request is being processed.",
            }

        try:
            # ── Step 2: Validate rate limits ──────────────────────────
            rate_check = await self.rate_limiter.check_and_record(user_id, chat_id)
            if not rate_check["allowed"]:
                return {
                    "status": "error",
                    "code": 429,
                    "reason": rate_check["reason"],
                    "retry_after": rate_check.get("retry_after", 60),
                }

            # ── Pre-fetch Chat Document (FETCH ONCE) ──────────────────
            chat_doc = await get_chat_by_id(self.db, chat_id)

            # ── Step 3: Validate token limits ─────────────────────────
            user_limit = await self.tokens.check_user_limits(user_id)
            if not user_limit["allowed"]:
                return {
                    "status": "error",
                    "code": 429,
                    "reason": user_limit["reason"],
                }

            chat_limit = await self.tokens.check_chat_limit(chat_id)
            if not chat_limit["allowed"]:
                return {
                    "status": "error",
                    "code": 429,
                    "reason": chat_limit["reason"],
                }

            # ── Step 4: Load memory ───────────────────────────────────
            # Pass chat_doc to avoid redundant fetch
            context = await self.memory.load_context(chat_id, chat_doc=chat_doc)

            # ── Step 4c: Load Long-term User Memories ─────────────────
            user_memories_text = ""
            try:
                # Bypass intent detector entirely.
                # Find all embeddings correlated directly to user message semantic meaning
                from db.mongo_helpers import get_relevant_memories
                db_memories = await get_relevant_memories(self.db, user_id, current_message=text)
                # Format as "- Entity Attribute: Value" for better LLM context
                memories = [
                    f"- {m.get('entity', '').capitalize()} {m.get('attribute', '').replace('_', ' ')}: {m.get('value', '')}" 
                    for m in db_memories
                ] if db_memories else []
                
                if memories:
                    user_memories_text = "\n\nUser Information:\n" + "\n".join(memories)
            except Exception as e:
                logger.warning(f"Failed to load user memories: {e}")

            # ── Step 5: Build token-aware LLM payload ─────────────────
            custom_system_prompt = SYSTEM_PROMPT + user_memories_text
            
            budget = self.tokens.calculate_budget(
                system_prompt=custom_system_prompt,
                summary=context["summary"],
                messages=context["messages"],
                user_message=text,
            )

            if not budget["fits"]:
                return {
                    "status": "error",
                    "code": 413,
                    "reason": budget["reason"],
                }

            # Check global TPM before calling LLM
            tpm_check = await self.rate_limiter.check_tpm(budget["total_input"])
            if not tpm_check["allowed"]:
                return {
                    "status": "error",
                    "code": 429,
                    "reason": tpm_check["reason"],
                    "retry_after": tpm_check.get("retry_after", 60),
                }

            # if attachments are present, convert message to multimodal parts
            user_content = text
            if attachments:
                parts = [{"text": text}]
                for a in attachments:
                    # Read binary content from local disk
                    filename = a.get("url").split("/")[-1]
                    file_path = os.path.join(os.getcwd(), "uploads", filename)
                    try:
                        with open(file_path, "rb") as f:
                            data = f.read()
                        parts.append({
                            "inline_data": {
                                "mime_type": a.get("content_type", "application/octet-stream"),
                                "data": data
                            }
                        })
                        logger.info(f"Loaded binary part for {filename}")
                    except Exception as e:
                        logger.error(f"Failed to load binary for {filename}: {e}")
                user_content = parts

            payload = self._build_payload(
                system_prompt=custom_system_prompt,
                summary=budget["trimmed_summary"],
                memory=budget["trimmed_messages"],
                user_message=user_content,
                persona=persona,
            )

            # ── DEBUG: Log final prompt to file ───────────────────────
            self._log_prompt_to_file(payload)

            # ── Step 6: Call LLM via model router ─────────────────
            try:
                llm_result = await self.router.generate(
                    task="chat", messages=payload
                )
            except RuntimeError:
                return {
                    "status": "error",
                    "code": 502,
                    "reason": "LLM service unavailable after retries.",
                }

            assistant_text = llm_result["content"]
            output_tokens = count_tokens(assistant_text)
            input_tokens = budget["total_input"]
            total_tokens = input_tokens + output_tokens

            # ── Step 7: Store user message ────────────────────────────
            user_tokens = count_tokens(text)
            user_msg_id = await self.memory.store_message(
                chat_id, user_id, "user", text, user_tokens, attachments=attachments
            )

            # ── Step 8: Store assistant message ───────────────────────
            # insert_message atomically increments chat stats + user token stats
            assistant_msg_id = await self.memory.store_message(
                chat_id, user_id, "assistant", assistant_text, output_tokens
            )

            # ── Step 9: Record token usage to Redis ───────────────────
            await self.tokens.record_usage(user_id, chat_id, total_tokens)

            # Adjust TPM reservation: estimate was input_tokens, actual is total
            await self.rate_limiter.adjust_tokens(input_tokens, total_tokens)

            # ── Step 11: Release lock EARLY ────────────────────────────
            #   We release the lock before enqueuing summarization or 
            #   triggering auto-naming. This allows the background worker
            #   to start immediately without waiting for these final steps.
            await release_chat_lock(self.redis, chat_id)
            lock_released = True

            # ── Step 10: Enqueue summarization if needed ──────────────
            try:
                # Use cached chat_doc or fetch fresh if needed
                if await self.memory.check_summarization_needed(chat_id, chat_doc=chat_doc):
                    logger.info(f"Summarization threshold reached for chat {chat_id}")
                    await self.memory.enqueue_summarization(chat_id, chat_doc=chat_doc)
            except Exception as e:
                logger.error(f"Failed to enqueue summarization for chat {chat_id}: {e}")

            # ── Step 12: Auto-naming (on 1st message) ────────────────
            try:
                # chat_doc was pre-fetched at Step 2; if not, we get it now
                if not chat_doc:
                    chat_doc = await get_chat_by_id(self.db, chat_id)
                
                if chat_doc and chat_doc.get("title") == "New Chat":
                    asyncio.create_task(self._auto_name_chat(chat_id, text))
            except Exception as e:
                logger.warning(f"Failed to trigger auto-naming for chat {chat_id}: {e}")

            # ── Step 13: Background Memory Extraction ────────────────
            try:
                msg_count = chat_doc.get("messageCount", 0) if chat_doc else 0
                last_active = chat_doc.get("lastActive") if chat_doc else None
                
                if self.memory_extractor.should_trigger_memory_extraction(
                    message_count=msg_count, 
                    last_message_timestamp=last_active, 
                    conversation_active=True,    # handle_message implies active
                    current_message=text,        # signal-based primary trigger
                ):
                    extraction_buffer = context["messages"] + [
                        {"role": "user", "content": text}, 
                        {"role": "assistant", "content": assistant_text}
                    ]
                    asyncio.create_task(
                        self.memory_extractor.extract_memories(
                            str(user_id), 
                            str(chat_id), 
                            extraction_buffer,
                            self._call_llm_with_retry
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to trigger memory extraction for chat {chat_id}: {e}")

            return {
                "status": "ok",
                "content": assistant_text,
                "message_id": str(assistant_msg_id),
                "user_message_id": str(user_msg_id),
                "token_usage": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": total_tokens,
                },
                "memory_source": context["source"],
            }

        except Exception as e:
            logger.error(f"Unexpected error in handle_message: {e}", exc_info=True)
            return {
                "status": "error",
                "code": 500,
                "reason": f"Internal error: {str(e)}",
            }

        finally:
            # ── Step 11: Release lock (ALWAYS runs if not already released)
            if not lock_released:
                await release_chat_lock(self.redis, chat_id)

    # ── LLM Call with Retry ───────────────────────────────────────────

    async def _call_llm_with_retry(
        self, payload: list[dict], temperature: float = 0.7
    ) -> dict | None:
        """
        Call LLM with exponential backoff on failure.

        Retry schedule: 1s → 2s → 4s → 8s (max 4 attempts).

        Returns the LLM response dict or None if all retries exhausted.
        """
        for attempt in range(MAX_RETRIES):
            try:
                result = await call_llm_async(payload, temperature)
                return result

            except RuntimeError as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "rate" in error_str
                is_timeout = "timeout" in error_str

                if is_rate_limit or is_timeout:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        f"LLM call attempt {attempt + 1}/{MAX_RETRIES} failed "
                        f"({'rate limit' if is_rate_limit else 'timeout'}), "
                        f"retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                else:
                    # Non-retryable error
                    logger.error(f"LLM call failed (non-retryable): {e}")
                    return None

            except Exception as e:
                logger.error(f"Unexpected LLM error: {e}")
                return None

        logger.error(f"LLM call failed after {MAX_RETRIES} retries")
        return None

    # ── Payload Builder ───────────────────────────────────────────────

    async def _auto_name_chat(self, chat_id: str, first_message: str):
        """
        Generate a 3-word title for a new chat based on the first message.
        Runs in the background to avoid adding latency to the chat response.
        """
        try:
            prompt = (
                "Generate a 3-word descriptive summary for the following user message. "
                "Output ONLY the 3 words, no punctuation unless necessary. "
                "The summary should be suitable as a short chat title. "
                f"\n\nMessage: {first_message}"
            )
            
            # Use small/fast model task for title generation
            result = await self.router.generate(
                task="summary", 
                messages=[{"role": "user", "content": prompt}]
            )
            
            title = result["content"].strip()
            # Basic cleanup (limit to 3-5 words just in case)
            title = " ".join(title.split()[:5])
            
            await update_chat_title(self.db, chat_id, title)
            # Publish title update so frontend can reflect it in real-time
            try:
                from utils.redis_client import get_redis_client
                import json as _json
                r = get_redis_client()
                payload = _json.dumps({"type": "title", "title": title})
                await r.publish(f"chat:{chat_id}:events", payload)
                # Also publish updated chat list for the owning user (best-effort)
                try:
                    from db.mongo_helpers import get_chat_by_id, get_user_chats
                    chat_doc = await get_chat_by_id(self.db, chat_id)
                    if chat_doc and chat_doc.get("userId"):
                        user_id_str = str(chat_doc.get("userId"))
                        chats = await get_user_chats(self.db, chat_doc.get("userId"))
                        chats_payload = [
                            {
                                "chat_id": str(c["_id"]),
                                "title": c.get("title", "Untitled"),
                                "message_count": c.get("messageCount", 0),
                                "total_tokens": c.get("totalTokens", 0),
                                "last_active": c.get("lastActive").isoformat() if c.get("lastActive") else None,
                            }
                            for c in chats
                        ]
                        await r.publish(f"user:{user_id_str}:events", _json.dumps({"type": "chat_list", "chats": chats_payload}))
                except Exception:
                    pass
            except Exception:
                pass

            logger.info(f"Auto-named chat {chat_id} -> '{title}'")
            
        except Exception as e:
            logger.warning(f"Auto-naming failed for chat {chat_id}: {e}")

    def _log_prompt_to_file(self, payload: list[dict]):
        """
        Debug helper: Overwrites 'last_prompt_debug.txt' with the 
        exact payload being sent to the LLM API.
        """
        try:
            debug_path = os.path.join(os.getcwd(), "last_prompt_debug.txt")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(f"=== LLM PROMPT DEBUGGER ===\n")
                f.write(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n")
                f.write(f"Total Messages: {len(payload)}\n")
                f.write("-" * 40 + "\n\n")
                for i, msg in enumerate(payload):
                    role = msg.get("role", "unknown").upper()
                    content = msg.get("content", "")
                    f.write(f"[{i}] ROLE: {role}\n")
                    f.write(f"CONTENT: {content}\n")
                    f.write("-" * 20 + "\n")
            logger.info(f"Logged LLM prompt to {debug_path}")
        except Exception as e:
            logger.error(f"Failed to write prompt debug file: {e}")

    def _build_payload(
        self,
        system_prompt: str,
        summary: str | None,
        memory: list[dict],
        user_message: str | list[dict],
        persona: str = "default",
    ) -> list[dict]:
        """
        Build the LLM-ready messages array.

        Structure:
          1. System prompt (with summary embedded if available)
          2. Recent memory messages
          3. User's new message (can be string or list of parts)
        """
        messages = []

        # System prompt + Persona Instructions
        persona_data = PERSONAS.get(persona, PERSONAS["default"])
        system_content = f"{system_prompt}\n\nBEHAVIORAL CONSTRAINTS:\n{persona_data['instructions']}"
        
        if summary:
            system_content += f"\n\nConversation summary so far:\n{summary}"

        messages.append({"role": "system", "content": system_content})

        # Recent memory
        for msg in memory:
            # Memory messages are currently always strings in DB
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        # Current user message
        messages.append({"role": "user", "content": user_message})

        return messages
