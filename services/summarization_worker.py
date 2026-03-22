# services/summarization_worker.py
#
# Background worker for async summarization.
#
# Polls a Redis queue (queue:summarization) and processes jobs
# independently from the request path.
#
# Architecture: Hierarchical Chunk Summaries
#   Instead of building a "summary of summary", each batch of messages
#   produces its own chunk document in `summary_chunks`. This prevents
#   summary drift. A meta-summary is generated periodically from all chunks.
#
# Safety:
#   - Acquires a dedicated summarization_lock:{chatId} in Redis
#   - enqueue_summarization checks this lock before enqueueing
#   - Re-checks thresholds before processing (idempotency guard)
#   - No message deletion until chunk summary is successfully stored
#   - Atomic counter adjustments via $inc
#
# Usage:
#   python -m services.summarization_worker
#   Or as asyncio task: asyncio.create_task(run_worker(db, redis))

import asyncio
import logging
from bson import ObjectId
from utils.token_counter import count_tokens
from utils.gemini_api import call_llm_async
from db.redis_helpers import (
    dequeue_summarization_job,
    acquire_summarization_lock,
    release_summarization_lock,
    clear_recent_messages,
    invalidate_chat_summary,
)
from db.mongo_helpers import (
    get_chat_by_id,
    get_last_messages,
    update_chat_summary,
    soft_delete_messages,
    decrement_chat_stats,
    insert_summary_chunk,
    get_summary_chunks,
    get_summary_chunk_count,
    get_next_chunk_index,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
SUMMARIZE_TOKEN_THRESHOLD = 10_000
SUMMARIZE_MSG_THRESHOLD   = 80
SUMMARY_MAX_TOKENS        = 3000
META_SUMMARY_EVERY_N_CHUNKS = 5   # Generate a meta-summary every N chunks


async def _generate_chunk_summary(messages: list) -> str:
    """
    Generate a clean, standalone summary of a single batch of messages.
    No previous summary is injected — each chunk is self-contained.
    """
    convo_text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    prompt = [
        {
            "role": "system",
            "content": (
                "You are a summarization assistant. Produce a concise, standalone summary "
                "of the conversation segment below. Preserve key facts, decisions, and "
                "named entities. Do NOT reference 'previous summaries'. Maximum 300 words.\n\n"
                "Format your output exactly as follows:\n"
                "SUMMARY:\n[Your summary here]\n\n"
                "ENTITIES:\n- [Entity 1]\n- [Entity 2]"
            ),
        },
        {
            "role": "user",
            "content": f"Conversation segment to summarize:\n{convo_text}",
        },
    ]
    result = await call_llm_async(prompt, temperature=0.3)
    return result.get("content", "").strip()


async def _generate_meta_summary(chunks: list) -> str:
    """
    Combine all chunk summaries into a single coherent meta-summary.
    Called when the chunk count crosses META_SUMMARY_EVERY_N_CHUNKS.
    """
    combined = "\n\n---\n\n".join(
        f"[Chunk {c['chunkIndex'] + 1}]\n{c['summary']}" for c in chunks
    )
    prompt = [
        {
            "role": "system",
            "content": (
                "You are a summarization assistant. You are given several sequential "
                "conversation summaries. Merge them into one coherent, concise summary "
                "that preserves all important facts, decisions, and context. Maximum 500 words."
            ),
        },
        {
            "role": "user",
            "content": f"Chunk summaries to merge:\n\n{combined}",
        },
    ]
    result = await call_llm_async(prompt, temperature=0.3)
    return result.get("content", "").strip()


async def process_job(db, redis, job: dict):
    """
    Process a single summarization job.

    Steps:
      1. Acquire dedicated summarization lock (prevents concurrent runs + duplicate jobs)
      2. Re-check thresholds (idempotency guard)
      3. Load oldest 50% of messages
      4. Generate a standalone chunk summary (no previous-summary injection)
      5. Store chunk in summary_chunks collection
      6. Soft-delete the summarized messages
      7. Adjust chat counters (atomic $inc)
      8. Invalidate Redis cache
      9. If chunk count crosses META_SUMMARY_EVERY_N_CHUNKS, generate a meta-summary
         and store it in chats.summary
    """
    chat_id = job["chat_id"]
    logger.info(f"[Worker] Processing summarization job for chat {chat_id}")

    # ── Step 1: Acquire summarization lock ───────────────────────────────
    locked = await acquire_summarization_lock(redis, chat_id)
    if not locked:
        logger.warning(
            f"[Worker] Summarization already in progress for chat {chat_id} — skipping."
        )
        return False

    try:
        # ── Step 2: Re-check thresholds (idempotency guard) ──────────────
        chat = await get_chat_by_id(db, chat_id)
        if not chat:
            logger.error(f"[Worker] Chat {chat_id} not found — skipping")
            return True

        total_tokens  = chat.get("totalTokens", 0)
        message_count = chat.get("messageCount", 0)

        if total_tokens <= SUMMARIZE_TOKEN_THRESHOLD and message_count <= SUMMARIZE_MSG_THRESHOLD:
            logger.info(
                f"[Worker] Chat {chat_id} no longer needs summarization "
                f"(tokens={total_tokens}, msgs={message_count}) — skipping"
            )
            return True

        # ── Step 3: Load all messages, oldest first ───────────────────────
        all_messages = await get_last_messages(db, chat_id, limit=1000)
        if len(all_messages) < 10:
            logger.info(f"[Worker] Chat {chat_id}: too few messages ({len(all_messages)}), skipping")
            return True

        # Summarize the oldest roughly 50%
        split_idx = len(all_messages) // 2
        
        # Seek an assistant message to serve as a natural turning point boundary
        for i in range(split_idx, 0, -1):
            if all_messages[i - 1].get("role") == "assistant":
                split_idx = i
                break

        to_summarize = all_messages[:split_idx]

        # ── Step 4: Generate standalone chunk summary ─────────────────────
        try:
            chunk_summary_text = await _generate_chunk_summary(to_summarize)
        except Exception as e:
            logger.error(f"[Worker] LLM call for chunk summary failed for chat {chat_id}: {e}")
            return False  # fail-safe — no data lost

        chunk_tokens = count_tokens(chunk_summary_text)
        if chunk_tokens > SUMMARY_MAX_TOKENS:
            chunk_summary_text = chunk_summary_text[:SUMMARY_MAX_TOKENS * 4]
            chunk_tokens = count_tokens(chunk_summary_text)

        # ── Step 5: Store chunk in summary_chunks ─────────────────────────
        start_msg_id = to_summarize[0].get("_id")
        end_msg_id   = to_summarize[-1].get("_id")
        chunk_index  = await get_next_chunk_index(db, chat_id)

        await insert_summary_chunk(
            db, chat_id,
            chunk_index  = chunk_index,
            start_msg_id = start_msg_id,
            end_msg_id   = end_msg_id,
            summary      = chunk_summary_text,
            tokens       = chunk_tokens,
        )
        logger.info(f"[Worker] Stored chunk {chunk_index} for chat {chat_id} ({chunk_tokens} tokens)")

        # ── Step 6: Soft-delete summarized messages ───────────────────────
        deleted_ids    = [m["_id"] for m in to_summarize]
        deleted_tokens = sum(m.get("tokens", 0) for m in to_summarize)
        await soft_delete_messages(db, deleted_ids)
        await decrement_chat_stats(db, chat_id, len(deleted_ids), deleted_tokens)

        # ── Step 7: Invalidate Redis cache ────────────────────────────────
        await asyncio.gather(
            clear_recent_messages(redis, chat_id),
            invalidate_chat_summary(redis, chat_id)
        )
        logger.info(
            f"[Worker] Summarization complete: soft-deleted {len(deleted_ids)} messages "
            f"({deleted_tokens} tokens), chunk #{chunk_index}"
        )

        # ── Step 8: Meta-summary if enough chunks accumulated ─────────────
        chunk_count = await get_summary_chunk_count(db, chat_id)
        if chunk_count > 0 and chunk_count % META_SUMMARY_EVERY_N_CHUNKS == 0:
            logger.info(f"[Worker] Generating meta-summary ({chunk_count} chunks) for chat {chat_id}")
            try:
                all_chunks   = await get_summary_chunks(db, chat_id, limit=chunk_count)
                meta_text    = await _generate_meta_summary(all_chunks)
                meta_tokens  = count_tokens(meta_text)
                await update_chat_summary(db, chat_id, meta_text, meta_tokens)
                logger.info(f"[Worker] Meta-summary stored for chat {chat_id} ({meta_tokens} tokens)")
            except Exception as e:
                logger.error(f"[Worker] Meta-summary generation failed for chat {chat_id}: {e}")
                # Non-fatal — chunks are already stored safely

        return True

    except Exception as e:
        logger.error(f"[Worker] Unexpected error processing chat {chat_id}: {e}", exc_info=True)
        return False

    finally:
        await release_summarization_lock(redis, chat_id)


async def run_worker(db, redis, *, shutdown_event: asyncio.Event | None = None):
    """
    Main worker loop. Continuously polls the summarization queue.

    Args:
        db: Motor database instance.
        redis: Async Redis client.
        shutdown_event: Optional asyncio.Event — when set, exits gracefully.
    """
    logger.info("[Worker] Summarization worker started")

    while True:
        if shutdown_event and shutdown_event.is_set():
            logger.info("[Worker] Shutdown event received — exiting")
            break

        try:
            job = await dequeue_summarization_job(redis, timeout=5)
            if job is None:
                continue

            logger.info(f"[Worker] Dequeued job: {job}")
            await process_job(db, redis, job)

        except Exception as e:
            logger.error(f"[Worker] Error in worker loop: {e}", exc_info=True)
            await asyncio.sleep(2)


# ── Entry point for standalone execution ──────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    from utils.mongo_client import get_mongo_db
    from utils.redis_client import get_redis_client

    db    = get_mongo_db()
    redis = get_redis_client()

    logger.info("Starting summarization worker (standalone mode)...")
    logger.info("Press Ctrl+C to stop.\n")

    try:
        asyncio.run(run_worker(db, redis))
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
        sys.exit(0)
