import asyncio
import logging
import os
import signal
from dotenv import load_dotenv

load_dotenv()

from utils.mongo_client import get_mongo_db
from utils.redis_client import get_redis_client
from db.redis_helpers import dequeue_memory_extraction_job
from db.mongo_helpers import get_last_messages, decay_user_memories
from services.user_memory_extractor import UserMemoryExtractor
from utils.gemini_api import call_llm_async
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("MemoryWorker")

# Global flag for graceful shutdown
_running = True


def handle_shutdown(signum, frame):
    global _running
    logger.info(f"Received signal {signum}, scheduling shutdown...")
    _running = False


async def run_worker(db, redis, shutdown_event: getattr(asyncio, "Event", None) = None):
    """
    Main background loop for processing memory extraction jobs.
    Runs continuously until shutdown_event is set.
    """
    logger.info("[MemoryWorker] Starting memory extraction background processor...")
    extractor = UserMemoryExtractor(db, redis)
    last_prune_time = 0

    while True:
        if shutdown_event and shutdown_event.is_set():
            logger.info("[MemoryWorker] Shutdown event detected. Exiting loop.")
            break

        # Periodic Pruning: Run every 1 hour to clean up context noise
        current_time = time.time()
        if current_time - last_prune_time > 3600:
            try:
                logger.info("[MemoryWorker] Running periodic memory pruning...")
                deleted = await decay_user_memories(db)
                if deleted > 0:
                    logger.info(f"[MemoryWorker] Pruned {deleted} noisy memories.")
                last_prune_time = current_time
            except Exception as e:
                logger.error(f"[MemoryWorker] Pruning failed: {e}")

        try:
            job = await dequeue_memory_extraction_job(redis)
            if not job:
                await asyncio.sleep(0.5)
                continue

            user_id = job["userId"]
            chat_id = job["chatId"]
            logger.info(f"[MemoryWorker] Selected chat {chat_id} for user {user_id} for memory extraction")

            # ── Execute Extraction ──────────────────────────────────────────
            # Fetch last 20 messages to give the extractor context
            recent_messages = await get_last_messages(db, chat_id, limit=20)
            
            if recent_messages:
                await extractor.extract_memories(
                    user_id=user_id,
                    chat_id=chat_id,
                    recent_messages=recent_messages,
                    llm_call_func=call_llm_async,
                    window_size=20
                )
            
            logger.info(f"[MemoryWorker] Finished extraction for chat {chat_id}")

        except asyncio.CancelledError:
            logger.info("[MemoryWorker] Task cancelled. Exiting loop.")
            break
        except Exception as e:
            logger.error(f"[MemoryWorker] Error processing job: {e}", exc_info=True)
            await asyncio.sleep(5)  # Backoff on error


async def main():
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    db = get_mongo_db()
    redis = get_redis_client()
    
    # Run the processor until an external signal sets _running to False
    task = asyncio.create_task(run_worker(db, redis))
    while _running:
        await asyncio.sleep(1)
        
    logger.info("[MemoryWorker] Shutting down cleanly.")
    task.cancel()
    
    # Wait briefly for task cancellation to complete
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

if __name__ == "__main__":
    asyncio.run(main())
