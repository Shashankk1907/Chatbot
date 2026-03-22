#!/usr/bin/env python3
"""
scripts/backfill_memories.py

One-off script to extract long-term memories from ALL existing chat history.

Run from the project root:
    source .venv/bin/activate
    python scripts/backfill_memories.py

What it does:
  1. Iterates every user in the `users` collection
  2. For each user, iterates their chats
  3. For each chat, fetches all messages and runs UserMemoryExtractor
  4. Stores extracted memories in `user_memories` (upserts, idempotent)
  5. Rate-limits between chats to avoid Gemini free-tier 429s
"""

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import motor.motor_asyncio
from bson import ObjectId

from utils.config import MONGO_URI
from utils.redis_client import get_redis_client
from utils.gemini_api import call_llm_async
from services.user_memory_extractor import UserMemoryExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")

# ── Config ─────────────────────────────────────────────────────────────────
DB_NAME             = os.getenv("MONGO_DB_NAME", "chat_database")
DELAY_BETWEEN_CHATS = 2.0    # seconds — respect free-tier RPM
MAX_MESSAGES_PER_CHAT = 200  # don't feed huge chats wholesale


async def backfill():
    # ── Connect ──────────────────────────────────────────────────────────
    logger.info("Connecting to MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    logger.info("Connecting to Redis...")
    redis = get_redis_client()

    extractor = UserMemoryExtractor(db, redis)

    # ── Iterate users ─────────────────────────────────────────────────────
    users = await db["users"].find({}, {"_id": 1, "email": 1}).to_list(length=None)
    logger.info(f"Found {len(users)} user(s) to process")

    total_memories = 0

    for user in users:
        user_id  = user["_id"]
        email    = user.get("email", str(user_id))

        chats = await db["chats"].find(
            {"userId": user_id},
            {"_id": 1, "title": 1, "messageCount": 1},
        ).to_list(length=None)

        if not chats:
            logger.info(f"  [{email}] No chats, skipping")
            continue

        logger.info(f"  [{email}] {len(chats)} chat(s)")

        for chat in chats:
            chat_id    = chat["_id"]
            title      = chat.get("title", "Untitled")
            msg_count  = chat.get("messageCount", 0)

            if msg_count == 0:
                logger.info(f"    [{title[:40]}] Empty chat, skipping")
                continue

            # Fetch messages (all roles; extractor will filter to user-only)
            cursor = (
                db["messages"]
                .find({"chatId": chat_id})
                .sort("createdAt", 1)
                .limit(MAX_MESSAGES_PER_CHAT)
            )
            messages = await cursor.to_list(length=MAX_MESSAGES_PER_CHAT)

            if not messages:
                continue

            # Serialize for the extractor
            serialized = []
            for m in messages:
                serialized.append({
                    "role":    m.get("role", "user"),
                    "content": m.get("content", ""),
                    "_id":     str(m.get("_id", "")),
                })

            user_msg_count = sum(1 for m in serialized if m["role"] == "user")
            logger.info(
                f"    [{title[:40]}] {msg_count} msgs total, "
                f"{user_msg_count} user msgs → extracting..."
            )

            try:
                await extractor.extract_memories(
                    user_id=str(user_id),
                    chat_id=str(chat_id),
                    recent_messages=serialized,
                    llm_call_func=call_llm_async,
                    window_size=MAX_MESSAGES_PER_CHAT,
                )
                logger.info(f"    [{title[:40]}] ✅ Done")
            except Exception as e:
                logger.error(f"    [{title[:40]}] ❌ Error: {e}")

            # Respect Gemini free-tier rate limits
            await asyncio.sleep(DELAY_BETWEEN_CHATS)

        # Brief pause between users
        await asyncio.sleep(1.0)

    # ── Summary ───────────────────────────────────────────────────────────
    memory_count = await db["user_memories"].count_documents({})
    logger.info(f"\n✅ Backfill complete. Total memories in DB: {memory_count}")

    await redis.aclose()
    client.close()


if __name__ == "__main__":
    asyncio.run(backfill())
