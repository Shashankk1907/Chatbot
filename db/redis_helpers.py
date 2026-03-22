# db/redis_helpers.py
#
# Async Redis Data Access Layer
#
# Key patterns:
#   session:{userId}              — user session (TTL 24h)
#   chat:{chatId}:recent          — sliding window of recent messages (TTL 30m)
#   rate:global:rpm               — global requests-per-minute counter
#   rate:global:tpm               — global tokens-per-minute counter
#   rate:user:{userId}:rpm        — per-user requests-per-minute
#   rate:user:{userId}:daily      — per-user daily request count
#   rate:chat:{chatId}:cooldown   — per-chat cooldown (2s debounce)
#   tokens:user:{userId}:today    — daily token counter per user (TTL 24h)
#   tokens:user:{userId}:lifetime — lifetime token counter per user (no TTL)
#   tokens:chat:{chatId}          — lifetime token counter per chat
#   lock:chat:{chatId}            — concurrency lock (SETNX, 10s TTL)
#
# All helpers receive the async Redis client as the first argument.
# No connections are created inside any helper function.

import json

# ── TTL Constants ──────────────────────────────────────────────────────────
SESSION_TTL = 86400         # 24 hours
RECENT_MSG_TTL = 1800       # 30 minutes inactivity
RECENT_MSG_LIMIT = 30       # max messages in sliding window
RPM_TTL = 60                # 1 minute
DAILY_TTL = 86400           # 24 hours
COOLDOWN_TTL = 2            # 2 seconds
LOCK_TTL = 60               # 60 seconds (increased from 10s to handle longer ops)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

async def set_session(r, user_id: str, data: dict):
    """Store a user session. Auto-expires after 24 hours."""
    key = f"session:{user_id}"
    await r.set(key, json.dumps(data), ex=SESSION_TTL)


async def get_session(r, user_id: str) -> dict | None:
    """Retrieve a user session. Returns None if expired/missing."""
    key = f"session:{user_id}"
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def delete_session(r, user_id: str):
    """Explicitly delete a user session (logout)."""
    key = f"session:{user_id}"
    await r.delete(key)


# ═══════════════════════════════════════════════════════════════════════════
# 2. ACTIVE CHAT SLIDING WINDOW
# ═══════════════════════════════════════════════════════════════════════════

async def push_recent_message(r, chat_id: str, message: dict):
    """
    Push a message to the sliding window for a chat.
    Uses LPUSH + LTRIM to keep only the last RECENT_MSG_LIMIT messages.
    Resets TTL on each push (30 min inactivity timer).

    Concurrency note: LPUSH + LTRIM is safe — Redis commands are atomic.
    Worst case under race: window briefly has LIMIT+1 items until LTRIM runs.
    """
    key = f"chat:{chat_id}:recent"
    await r.lpush(key, json.dumps(message))
    await r.ltrim(key, 0, RECENT_MSG_LIMIT - 1)
    await r.expire(key, RECENT_MSG_TTL)
    # Publish a lightweight event so subscribers (SSE/WebSocket) can react in real-time
    try:
        event_payload = json.dumps({"type": "message", "message": message})
        await r.publish(f"chat:{chat_id}:events", event_payload)
    except Exception:
        # best-effort; failures should not break the message push
        pass


async def get_recent_messages(r, chat_id: str) -> list[dict]:
    """
    Get the sliding window messages for a chat.
    Returns oldest-first order (reversed from LPUSH storage).
    """
    key = f"chat:{chat_id}:recent"
    raw_list = await r.lrange(key, 0, -1)
    messages = [json.loads(item) for item in raw_list]
    messages.reverse()  # oldest first
    return messages


async def clear_recent_messages(r, chat_id: str):
    """Clear the sliding window (e.g. on chat archive or reset)."""
    key = f"chat:{chat_id}:recent"
    await r.delete(key)


# ═══════════════════════════════════════════════════════════════════════════
# 2b. CHAT SUMMARY CACHING
# ═══════════════════════════════════════════════════════════════════════════

CHAT_SUMMARY_TTL = 1800  # 30 minutes (synced with RECENT_MSG_TTL)

async def get_cached_chat_summary(r, chat_id: str) -> dict | None:
    """
    Retrieve cached summary context (text + tokens) for a chat.
    Returns {"summary": str, "tokens": int} or None.
    """
    key = f"chat:{chat_id}:summary"
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def set_cached_chat_summary(r, chat_id: str, summary: str, tokens: int):
    """Cache the summary context for a chat."""
    key = f"chat:{chat_id}:summary"
    payload = json.dumps({"summary": summary, "tokens": tokens})
    await r.set(key, payload, ex=CHAT_SUMMARY_TTL)


async def invalidate_chat_summary(r, chat_id: str):
    """Invalidate the cached summary for a chat."""
    key = f"chat:{chat_id}:summary"
    await r.delete(key)


# ═══════════════════════════════════════════════════════════════════════════
# 3. RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════

async def increment_with_ttl(r, key: str, ttl: int) -> int:
    """
    Atomically increment a counter and set TTL if it's a new key.
    Returns the new count.

    Uses a pipeline for atomicity:
      INCR + EXPIRE are sent together so the key always has a TTL.
    """
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl, nx=True)  # nx=True: only set TTL if not already set
    results = await pipe.execute()
    return results[0]  # new count after INCR


async def check_rate_limit(r, user_id: str, chat_id: str) -> dict:
    """
    Check all rate limits before processing a message.
    Returns {"allowed": True} or {"allowed": False, "reason": "..."}.

    Checks (in order):
      1. Chat cooldown (2s between messages in same chat)
      2. User RPM (requests per minute)
      3. User daily limit
      4. Global RPM

    Does NOT increment counters — call increment_with_ttl separately
    after the request is accepted.
    """
    # 1. Chat cooldown — if key exists, the chat is in cooldown
    cooldown_key = f"rate:chat:{chat_id}:cooldown"
    if await r.exists(cooldown_key):
        return {"allowed": False, "reason": "Chat cooldown — wait 2 seconds."}

    # 2. User RPM (60 req/min soft limit)
    user_rpm_key = f"rate:user:{user_id}:rpm"
    user_rpm = await r.get(user_rpm_key)
    if user_rpm and int(user_rpm) >= 20:
        return {"allowed": False, "reason": "User rate limit — max 20 req/min."}

    # 3. User daily limit (1000 req/day for free tier)
    user_daily_key = f"rate:user:{user_id}:daily"
    user_daily = await r.get(user_daily_key)
    if user_daily and int(user_daily) >= 1000:
        return {"allowed": False, "reason": "Daily limit reached — max 1000 req/day."}

    # 4. Global RPM
    global_rpm_key = "rate:global:rpm"
    global_rpm = await r.get(global_rpm_key)
    if global_rpm and int(global_rpm) >= 100:
        return {"allowed": False, "reason": "Global rate limit — server busy."}

    return {"allowed": True}


async def record_request(r, user_id: str, chat_id: str):
    """
    Increment all rate-limit counters after a request is accepted.
    Call this AFTER check_rate_limit returns allowed=True.
    """
    await increment_with_ttl(r, f"rate:chat:{chat_id}:cooldown", COOLDOWN_TTL)
    await increment_with_ttl(r, f"rate:user:{user_id}:rpm", RPM_TTL)
    await increment_with_ttl(r, f"rate:user:{user_id}:daily", DAILY_TTL)
    await increment_with_ttl(r, "rate:global:rpm", RPM_TTL)


# ═══════════════════════════════════════════════════════════════════════════
# 4. TOKEN TRACKING
# ═══════════════════════════════════════════════════════════════════════════

async def track_tokens_user(r, user_id: str, tokens: int):
    """
    Track token usage for a user in Redis.
    Writes to BOTH daily (24h TTL) and lifetime (no TTL) counters.
    Redis is the real-time source; Mongo is updated via daily snapshot.
    """
    # Daily counter (auto-expires)
    daily_key = f"tokens:user:{user_id}:today"
    await increment_with_ttl(r, daily_key, DAILY_TTL)
    if tokens > 1:
        await r.incrby(daily_key, tokens - 1)

    # Lifetime counter (never expires — permanent accumulator)
    lifetime_key = f"tokens:user:{user_id}:lifetime"
    await r.incrby(lifetime_key, tokens)


async def track_tokens_chat(r, chat_id: str, tokens: int):
    """Track lifetime token usage for a chat. No expiry."""
    key = f"tokens:chat:{chat_id}"
    await r.incrby(key, tokens)


async def get_user_daily_tokens(r, user_id: str) -> int:
    """Get how many tokens a user has used today."""
    key = f"tokens:user:{user_id}:today"
    val = await r.get(key)
    return int(val) if val else 0


async def get_user_lifetime_tokens(r, user_id: str) -> int:
    """Get total lifetime tokens a user has consumed."""
    key = f"tokens:user:{user_id}:lifetime"
    val = await r.get(key)
    return int(val) if val else 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONCURRENCY LOCK
# ═══════════════════════════════════════════════════════════════════════════

async def acquire_chat_lock(r, chat_id: str) -> bool:
    """
    Try to acquire an exclusive lock on a chat session.
    Uses SETNX (SET if Not eXists) with a 10-second auto-expiry.

    Returns True if lock acquired, False if another request holds it.

    Safety:
      - SETNX + EX is atomic in Redis (single SET command with NX+EX flags).
      - 10s TTL prevents deadlocks if a request crashes without releasing.
    """
    key = f"lock:chat:{chat_id}"
    acquired = await r.set(key, "1", nx=True, ex=LOCK_TTL)
    return acquired is not None  # SET NX returns None if key already exists


async def release_chat_lock(r, chat_id: str):
    """
    Release the concurrency lock on a chat session.
    Always call this in a finally block after acquire_chat_lock.
    """
    key = f"lock:chat:{chat_id}"
    await r.delete(key)


# ═══════════════════════════════════════════════════════════════════════════
# 6. SUMMARIZATION JOB QUEUE
# ═══════════════════════════════════════════════════════════════════════════

SUMMARIZATION_QUEUE_KEY = "queue:summarization"


async def enqueue_summarization_job(r, chat_id: str, threshold_snapshot: dict):
    """
    Enqueue a summarization job for background processing.

    Uses RPUSH for FIFO ordering (worker uses BLPOP from the left).
    Job payload is JSON: {"chat_id": ..., "threshold_snapshot": {...}}.

    Idempotency: the worker re-checks thresholds before processing,
    so duplicate enqueues are safe (they become no-ops).
    """
    payload = json.dumps({
        "chat_id": chat_id,
        "threshold_snapshot": threshold_snapshot,
    })
    await r.rpush(SUMMARIZATION_QUEUE_KEY, payload)


async def dequeue_summarization_job(r, timeout: int = 5) -> dict | None:
    """
    Blocking dequeue of a summarization job.

    Uses BLPOP with a timeout — blocks until a job is available
    or timeout seconds elapse (returns None on timeout).

    Returns the parsed job dict or None.
    """
    result = await r.blpop(SUMMARIZATION_QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    # BLPOP returns (key, value) tuple
    _, payload = result
    return json.loads(payload)


# ═══════════════════════════════════════════════════════════════════════════
# 8. BACKGROUND JOBS & SUMMARIZATION LOCK
# ═══════════════════════════════════════════════════════════════════════════

SUMMARIZATION_LOCK_TTL = 120  # 2 minutes max for a summarization run

# ── Summarization ─────────────────────────────────────────────────────────


async def acquire_summarization_lock(r, chat_id: str) -> bool:
    """
    Acquire a dedicated summarization lock for a chat.
    This is separate from the per-request chat lock so that incoming messages
    can still be processed while summarization is running.

    Returns True if lock acquired, False if summarization already in progress.
    TTL of 120s prevents deadlocks if the worker crashes mid-run.
    """
    key = f"summarization_lock:{chat_id}"
    acquired = await r.set(key, "1", nx=True, ex=SUMMARIZATION_LOCK_TTL)
    return acquired is not None


async def release_summarization_lock(r, chat_id: str):
    """Release the summarization lock. Always call in a finally block."""
    key = f"summarization_lock:{chat_id}"
    await r.delete(key)


async def is_summarization_locked(r, chat_id: str) -> bool:
    """
    Check if summarization is currently running for a chat.
    Used by enqueue_summarization to avoid creating duplicate jobs.
    """
    key = f"summarization_lock:{chat_id}"
    return await r.exists(key) > 0


# ── Memory Extraction jobs ────────────────────────────────────────────────

async def enqueue_memory_extraction_job(r, user_id: str, chat_id: str):
    """
    Push a background job to extract memories for a user/chat.
    Uses a lightweight lock to prevent queueing duplicates for the same chat.
    """
    lock_key = f"lock:memory_extract:{chat_id}"
    acquired = await r.set(lock_key, "1", nx=True, ex=60) # 1 min lock
    if acquired:
        payload = json.dumps({"userId": str(user_id), "chatId": str(chat_id)})
        await r.lpush("queue:memory_extraction", payload)
        return True
    return False

async def dequeue_memory_extraction_job(r):
    """Pop the next memory extraction job."""
    result = await r.brpop("queue:memory_extraction", timeout=1)
    if result:
        payload = json.loads(result[1].decode("utf-8"))
        # Also clean up the queue lock so it can be re-queued later
        await r.delete(f"lock:memory_extract:{payload['chatId']}")
        return payload
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 9. TOKEN REVOCATION & REFRESH TOKENS
# ═══════════════════════════════════════════════════════════════════════════

async def store_refresh_token(r, user_id: str, jti: str, expires_in_seconds: int):
    """Store a valid refresh token JTI in Redis."""
    key = f"refresh:{user_id}:{jti}"
    await r.set(key, "valid", ex=expires_in_seconds)

async def validate_refresh_token(r, user_id: str, jti: str) -> bool:
    """Check if a refresh token JTI exists and is valid in Redis."""
    key = f"refresh:{user_id}:{jti}"
    return await r.exists(key) > 0

async def delete_refresh_token(r, user_id: str, jti: str):
    """Delete a refresh token JTI from Redis (e.g., on logout or refresh)."""
    key = f"refresh:{user_id}:{jti}"
    await r.delete(key)

async def blacklist_access_token(r, jti: str, expires_in_seconds: int):
    """Add an access token JTI to the blacklist until it naturally expires."""
    key = f"blacklist:access:{jti}"
    # Ensure minimum TTL of 1 second if it's about to expire
    ttl = max(1, expires_in_seconds)
    await r.set(key, "revoked", ex=ttl)

async def is_token_blacklisted(r, jti: str) -> bool:
    """Check if an access token JTI is in the blacklist."""
    key = f"blacklist:access:{jti}"
    return await r.exists(key) > 0
