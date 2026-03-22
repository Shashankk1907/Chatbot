# db/mongo_helpers.py
#
# Async MongoDB Data Access Layer
#
# All write operations use atomic operators ($inc, $set, $push).
# No full-document overwrites — safe under concurrency.
#
# Every helper receives `db` (Motor database instance) as the first argument
# so that no connections are created inside helpers.

from datetime import datetime, timezone
from bson import ObjectId


# ═══════════════════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════════════════

async def create_user(
    db, 
    email: str, 
    password_hash: str | None = None, 
    provider: str = "local", 
    provider_id: str | None = None,
    plan: str = "free"
):
    """Insert a new user document. Returns the inserted _id."""
    doc = {
        "email": email,
        "passwordHash": password_hash,
        "provider": provider,        # "local", "google", "github"
        "providerId": provider_id,  # Unique ID from OAuth provider
        "plan": plan,
        "createdAt": datetime.now(timezone.utc),
        "totalTokensUsed": 0,
        "dailyTokensUsed": 0,
        "lastActive": datetime.now(timezone.utc),
    }
    result = await db["users"].insert_one(doc)
    return result.inserted_id


async def get_user_by_provider(db, provider: str, provider_id: str):
    """Fetch a user by OAuth provider and providerId."""
    return await db["users"].find_one({
        "provider": provider,
        "providerId": provider_id
    })


async def get_user_by_email(db, email: str):
    """Fetch a user by email. Returns the document or None."""
    return await db["users"].find_one({"email": email})


async def get_user_by_id(db, user_id):
    """Fetch a user by ObjectId."""
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)
    return await db["users"].find_one({"_id": user_id})


async def increment_user_token_stats(db, user_id, tokens: int):
    """
    Atomically increment both totalTokensUsed and dailyTokensUsed.
    Also bumps lastActive.

    Concurrency-safe: uses $inc (atomic counter).
    """
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)

    await db["users"].update_one(
        {"_id": user_id},
        {
            "$inc": {
                "totalTokensUsed": tokens,
                "dailyTokensUsed": tokens,
            },
            "$set": {
                "lastActive": datetime.now(timezone.utc),
            },
        },
    )


async def reset_daily_tokens(db, user_id):
    """Reset dailyTokensUsed to 0 (call at midnight or via cron)."""
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)
    await db["users"].update_one(
        {"_id": user_id},
        {"$set": {"dailyTokensUsed": 0}},
    )


# ═══════════════════════════════════════════════════════════════════════════
# CHATS
# ═══════════════════════════════════════════════════════════════════════════

async def create_chat(db, user_id, title: str = "New Chat"):
    """Create a new chat session for a user. Returns the inserted _id."""
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)

    doc = {
        "userId": user_id,
        "title": title,
        "createdAt": datetime.now(timezone.utc),
        "lastActive": datetime.now(timezone.utc),
        "messageCount": 0,
        "totalTokens": 0,
        "summary": None,
        "summaryTokens": 0,
        "isArchived": False,
    }
    result = await db["chats"].insert_one(doc)
    return result.inserted_id


async def get_user_chats(db, user_id, include_archived: bool = False, limit: int = 50, skip: int = 0):
    """
    Get chats for a user, sorted by lastActive descending.
    Uses the compound index (userId, lastActive desc) — no collection scan.
    """
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)

    query = {"userId": user_id}
    if not include_archived:
        query["isArchived"] = False

    cursor = db["chats"].find(query).sort("lastActive", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def get_chat_by_id(db, chat_id):
    """Fetch a single chat by its ObjectId."""
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)
    return await db["chats"].find_one({"_id": chat_id})


async def is_chat_owner(db, chat_id, user_id) -> bool:
    """Verify if a chat belongs to a specific user (id-based check)."""
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)
        
    chat = await db["chats"].find_one({"_id": chat_id, "userId": user_id}, {"_id": 1})
    return chat is not None


async def increment_chat_stats(db, chat_id, tokens: int):
    """
    Atomically increment messageCount and totalTokens for a chat.
    Also bumps lastActive.

    Concurrency-safe: uses $inc (atomic counter) + $set.
    """
    def is_valid_oid(val):
        return isinstance(val, str) and len(val) == 24 and all(c in "0123456789abcdefABCDEF" for c in val)

    if is_valid_oid(chat_id):
        chat_id = ObjectId(chat_id)

    await db["chats"].update_one(
        {"_id": chat_id},
        {
            "$inc": {
                "messageCount": 1,
                "totalTokens": tokens,
            },
            "$set": {
                "lastActive": datetime.now(timezone.utc),
            },
        },
    )


async def decrement_chat_stats(db, chat_id, message_count: int, tokens: int):
    """
    Atomically decrement messageCount and totalTokens for a chat.
    Called after summarization to reflect the new "active window" size.
    """
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)

    await db["chats"].update_one(
        {"_id": chat_id},
        {
            "$inc": {
                "messageCount": -message_count,
                "totalTokens": -tokens,
            }
        },
    )


async def update_chat_summary(db, chat_id, summary: str, summary_tokens: int):
    """
    Store/update the rolling summary for a chat.
    Atomic $set — overwrites previous summary safely, while a $push history 
    retains a fallback mechanic to avoid poisoned context.
    """
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)

    await db["chats"].update_one(
        {"_id": chat_id},
        {
            "$set": {
                "summary": summary,
                "summaryTokens": summary_tokens,
            },
            "$push": {
                "summaryVersions": {
                    "summary": summary,
                    "createdAt": datetime.now(timezone.utc)
                }
            }
        },
    )


async def archive_chat(db, chat_id):
    """Soft-archive a chat (keeps data, hides from active list)."""
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)
    await db["chats"].update_one(
        {"_id": chat_id},
        {"$set": {"isArchived": True}},
    )


async def update_chat_title(db, chat_id, title: str):
    """Update the title of a chat session."""
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)
    await db["chats"].update_one(
        {"_id": chat_id},
        {"$set": {"title": title}},
    )


# ═══════════════════════════════════════════════════════════════════════════
# MESSAGES
# ═══════════════════════════════════════════════════════════════════════════

async def insert_message(db, chat_id, user_id, role: str, content: str, tokens: int, attachments: list | None = None):
    """
    Insert a single message document.
    Messages are stored as separate documents (not embedded in chat).

    Attachments may be provided as a list of metadata dicts {
        "filename": str,
        "url": str,
        "content_type": str,
    }. This is optional.

    After inserting, atomically updates chat stats (messageCount, totalTokens).
    User-level token tracking is handled in real-time by both Redis and Mongo (see TokenManager).
    """
    def is_valid_oid(val):
        return isinstance(val, str) and len(val) == 24 and all(c in "0123456789abcdefABCDEF" for c in val)

    if is_valid_oid(chat_id):
        chat_id = ObjectId(chat_id)
    if is_valid_oid(user_id):
        user_id = ObjectId(user_id)

    doc = {
        "chatId": chat_id,
        "userId": user_id,
        "role": role,
        "content": content,
        "tokens": tokens,
        "isSummarized": False,
        "createdAt": datetime.now(timezone.utc),
    }
    if attachments:
        # store attachments metadata array
        doc["attachments"] = attachments
    result = await db["messages"].insert_one(doc)

    # Atomically update chat stats only (user tokens tracked in Redis)
    await increment_chat_stats(db, chat_id, tokens)

    return result.inserted_id


async def get_last_messages(db, chat_id, limit: int = 30, include_summarized: bool = False):
    """
    Fetch the most recent `limit` active messages for a chat, oldest-first.
    If include_summarized is False (default), excludes summarized messages.
    Uses the compound index (chatId, isSummarized, createdAt).

    Returns a list of message dicts.
    """
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)

    # Exclude summarized messages unless requested; sort descending to get latest, then reverse
    query = {"chatId": chat_id}
    if not include_summarized:
        query["isSummarized"] = {"$ne": True}

    cursor = (
        db["messages"]
        .find(query)
        .sort("createdAt", -1)
        .limit(limit)
    )
    messages = await cursor.to_list(length=limit)
    messages.reverse()  # oldest first
    return messages


async def get_all_messages(db, chat_id, limit: int = 1000):
    """
    Fetch ALL messages for a chat (including summarized).
    Used by backfill scripts and migration tools.
    """
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)

    cursor = (
        db["messages"]
        .find({"chatId": chat_id})
        .sort("createdAt", 1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def soft_delete_messages(db, message_ids: list):
    """
    Soft-delete messages by marking them as summarized.
    Does NOT remove documents — preserves full history.

    Args:
        message_ids: list of ObjectId or str _id values
    """
    oids = [
        ObjectId(mid) if isinstance(mid, str) else mid
        for mid in message_ids
    ]
    result = await db["messages"].update_many(
        {"_id": {"$in": oids}},
        {"$set": {"isSummarized": True}},
    )
    return result.modified_count


# ═══════════════════════════════════════════════════════════════════════════
# USER MEMORIES (LONG-TERM)
# ═══════════════════════════════════════════════════════════════════════════

async def upsert_user_memory(db, user_id, memory_dict: dict, source_message_id: str | None = None):
    """
    Upsert a single structured long-term memory.
    Implements Conflict Resolution: if the same key appears again for the user,
    it pushes the old value to history and updates the current value, confidence, and timestamp.
    """
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)

    src_msg_oid = None
    if source_message_id:
        src_msg_oid = (
            ObjectId(source_message_id)
            if isinstance(source_message_id, str)
            else source_message_id
        )

    now = datetime.now(timezone.utc)
    
    # Phase 7: Structured Identity Filtering (User + Entity + Attribute)
    filter_q = {
        "userId": user_id, 
        "entity": memory_dict["entity"], 
        "attribute": memory_dict["attribute"]
    }
    existing = await db["user_memories"].find_one(filter_q)

    # Atomic upsert based on (userId, key). Overwrites the value and updates metadata.
    update_op = {
        "$set": {
            "value":      memory_dict["value"],
            "category":   memory_dict["category"],
            "importance": memory_dict["importance"],
            "confidence": memory_dict["confidence"],
            "status":     memory_dict.get("status", "pending"),
            "updatedAt":  now,
        },
        "$setOnInsert": {
            "createdAt":   now,
            "accessCount": 0,
        },
    }

    if src_msg_oid:
        update_op["$set"]["sourceMessageId"] = src_msg_oid

    if existing and existing.get("value") == memory_dict["value"]:
        # Reinforcement: exact match again restores trust!
        update_op["$set"]["status"] = "verified"
        if "$inc" not in update_op:
            update_op["$inc"] = {}
        update_op["$inc"]["reinforcements"] = 1
    elif existing and existing.get("value") != memory_dict["value"]:
        old_record = {
            "value": existing.get("value"),
            "updatedAt": existing.get("updatedAt", existing.get("createdAt")),
            "confidence": existing.get("confidence")
        }
        if "$push" not in update_op:
            update_op["$push"] = {}
        update_op["$push"]["history"] = old_record

    await db["user_memories"].update_one(filter_q, update_op, upsert=True)


def _score_memory(mem: dict, now: datetime, query_overlap: float = 0.0, cosine_sim: float = 0.0) -> float:
    """
    Composite relevance score for a single memory document.

    score = normalized_importance + (0.1 * recency_score) + query_overlap + (cosine_sim * 2.0)
    This ensures Recency does not override Importance. Semantic identities do not decay.
    """
    importance_raw = float(mem.get("importance", 5.0))
    normalized_importance = min(max(importance_raw / 10.0, 0.1), 1.0)

    # Semantic vs Episodic Decay: Identity and core facts do not structurally decay 
    if importance_raw >= 8:
        recency_score = 1.0
    else:
        updated_at = mem.get("updatedAt") or mem.get("createdAt") or now
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = (now - updated_at).total_seconds() / 86_400.0
        
        # Exponential decay: full score at 0 days, 0.5 at 30 days
        import math
        recency_score = math.exp(-age_days / 43.3)

    return normalized_importance + (0.1 * recency_score) + query_overlap + (cosine_sim * 2.0)


async def get_relevant_memories(db, user_id, current_message: str = ""):
    """
    Retrieve the most relevant memories using a dynamic context threshold via Embeddings.
    Uses query filtering + cosine similarity to prioritize what user is currently talking about.
    """
    from utils.embeddings import get_embedding
    import numpy as np
    
    if isinstance(user_id, str):
        user_id = ObjectId(user_id)

    # Resolve query embedding ahead of time to broadcast against facts
    query_emb = get_embedding(current_message) if current_message else []
    now = datetime.now(timezone.utc)

    # Retrieve all memory candidates for the user (inexpensive document scan)
    cursor = db["user_memories"].find({"userId": user_id})
    all_memories = await cursor.to_list(length=100)

    if not all_memories:
        return []

    scored_candidates = []
    
    for mem in all_memories:
        mem_emb = mem.get("embedding", [])
        cosine_sim = 0.0
        
        # Compute Cosine Similarity
        if query_emb and mem_emb and len(query_emb) == len(mem_emb):
            A = np.array(query_emb)
            B = np.array(mem_emb)
            norm = np.linalg.norm(A) * np.linalg.norm(B)
            if norm > 0:
                cosine_sim = float(np.dot(A, B) / norm)
                
        # Query Overlap calculation (Phase 7: Entity/Attribute oriented)
        query_overlap = 0.0
        if current_message:
            import re
            query_words = set(re.findall(r'\b[a-z]+\b', current_message.lower()))
            entity_words = set(re.findall(r'\b[a-z]+\b', mem.get("entity", "").lower()))
            attr_words = set(re.findall(r'\b[a-z]+\b', mem.get("attribute", "").lower()))
            value_words = set(re.findall(r'\b[a-z]+\b', mem.get("value", "").lower()))
            
            if query_words & entity_words:
                query_overlap += 0.3
            if query_words & attr_words:
                query_overlap += 0.4
            if query_words & value_words:
                query_overlap += 0.5

        final_score = _score_memory(mem, now, query_overlap, cosine_sim)
        
        # Dynamic Budget Cutoff: Include anything highly relevant OR highly important
        if cosine_sim > 0.40 or query_overlap > 0 or float(mem.get("importance", 5)) >= 8:
            scored_candidates.append((final_score, mem))

    # Fine-grained sort
    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    top = [m[1] for m in scored_candidates[:20]]

    # Atomically update access stats
    if top:
        memory_ids = [m["_id"] for m in top]
        await db["user_memories"].update_many(
            {"_id": {"$in": memory_ids}},
            {
                "$inc": {"accessCount": 1},
                "$set": {"lastAccessed": now},
            },
        )

    return top




async def decay_user_memories(db):
    """
    Delete unverified, low importance memories that haven't been accessed in 30 days.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    
    result = await db["user_memories"].delete_many({
        "status": "pending",
        "importance": {"$lt": 6},
        "$or": [
            {"lastAccessed": {"$lt": cutoff}},
            {"createdAt": {"$lt": cutoff}, "lastAccessed": {"$exists": False}}
        ]
    })
    return result.deleted_count


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY CHUNKS (Hierarchical Summarization)
# ═══════════════════════════════════════════════════════════════════════════

async def insert_summary_chunk(
    db, chat_id, chunk_index: int, start_msg_id, end_msg_id, summary: str, tokens: int
):
    """
    Store a single per-batch summary chunk in the summary_chunks collection.
    Each chunk covers a specific range of messages (startMsgId → endMsgId).
    Chunks are indexed by chunkIndex for ordered retrieval.
    """
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)
    if isinstance(start_msg_id, str):
        start_msg_id = ObjectId(start_msg_id)
    if isinstance(end_msg_id, str):
        end_msg_id = ObjectId(end_msg_id)

    doc = {
        "chatId": chat_id,
        "chunkIndex": chunk_index,
        "startMsgId": start_msg_id,
        "endMsgId": end_msg_id,
        "summary": summary,
        "tokens": tokens,
        "createdAt": datetime.now(timezone.utc),
    }
    result = await db["summary_chunks"].insert_one(doc)
    return result.inserted_id


async def get_summary_chunks(db, chat_id, limit: int = 20) -> list:
    """
    Retrieve summary chunks for a chat, sorted by chunkIndex ascending.
    Returns the most recent `limit` chunks.
    """
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)

    cursor = db["summary_chunks"].find({"chatId": chat_id}).sort("chunkIndex", 1).skip(
        max(0, await db["summary_chunks"].count_documents({"chatId": chat_id}) - limit)
    )
    return await cursor.to_list(length=limit)


async def get_summary_chunk_count(db, chat_id) -> int:
    """Count how many summary chunks exist for this chat."""
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)
    return await db["summary_chunks"].count_documents({"chatId": chat_id})


async def get_next_chunk_index(db, chat_id) -> int:
    """Get the next chunkIndex to use (max existing + 1, or 0 if none)."""
    if isinstance(chat_id, str):
        chat_id = ObjectId(chat_id)

    cursor = db["summary_chunks"].find({"chatId": chat_id}).sort("chunkIndex", -1).limit(1)
    docs = await cursor.to_list(length=1)
    if not docs:
        return 0
    return docs[0]["chunkIndex"] + 1
