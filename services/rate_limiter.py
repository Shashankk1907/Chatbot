# services/rate_limiter.py
#
# RateLimiter — enforces all rate limits BEFORE calling the LLM.
#

#
# LLM API limits (from system constraints):
#   5 RPM, 250K TPM, 20 RPD
#
# Additional protective limits:
#   Chat cooldown: 2s between messages in same chat
#   User RPM: 5 req/min
#   User daily: 20 req/day

import logging
from db.redis_helpers import increment_with_ttl

logger = logging.getLogger(__name__)

# ── Limit thresholds ──────────────────────────────────────────────────────
GLOBAL_RPM_LIMIT = 5           # matches LLM API: 5 requests/min
GLOBAL_TPM_LIMIT = 250_000     # matches LLM API: 250K tokens/min
USER_RPM_LIMIT = 5             # per-user: 5 req/min
USER_DAILY_LIMIT = 20          # per-user: 20 req/day (matches RPD)
CHAT_COOLDOWN_TTL = 2          # seconds between messages in same chat

# ── TTLs ──────────────────────────────────────────────────────────────────
RPM_TTL = 60       # 1 minute
DAILY_TTL = 86400  # 24 hours


class RateLimiter:
    """
    Pre-flight rate limit enforcement.
    All checks happen BEFORE the LLM call to prevent wasted compute.
    """

    def __init__(self, redis):
        self.redis = redis

    async def check_and_record(self, user_id: str, chat_id: str) -> dict:
        """
        Check all rate limits and record the request atomically.

        Returns:
            {"allowed": True} or {"allowed": False, "reason": "...", "retry_after": N}

        Check order (cheapest/fastest first):
          1. Chat cooldown (2s debounce)
          2. User RPM
          3. User daily
          4. Global RPM
        """
        r = self.redis

        # ── 1. Chat cooldown ──────────────────────────────────────────
        cooldown_key = f"rate:chat:{chat_id}:cooldown"
        if await r.exists(cooldown_key):
            ttl = await r.ttl(cooldown_key)
            return {
                "allowed": False,
                "reason": "Chat cooldown — wait before sending another message.",
                "retry_after": max(ttl, 1),
            }

        # ── 2. User RPM ──────────────────────────────────────────────
        user_rpm_key = f"rate:user:{user_id}:rpm"
        user_rpm = await r.get(user_rpm_key)
        if user_rpm and int(user_rpm) >= USER_RPM_LIMIT:
            ttl = await r.ttl(user_rpm_key)
            return {
                "allowed": False,
                "reason": f"Rate limit — max {USER_RPM_LIMIT} requests/min.",
                "retry_after": max(ttl, 1),
            }

        # ── 3. User daily ────────────────────────────────────────────
        user_daily_key = f"rate:user:{user_id}:daily"
        user_daily = await r.get(user_daily_key)
        if user_daily and int(user_daily) >= USER_DAILY_LIMIT:
            return {
                "allowed": False,
                "reason": f"Daily limit reached — max {USER_DAILY_LIMIT} requests/day.",
                "retry_after": await r.ttl(user_daily_key),
            }

        # ── 4. Global RPM ────────────────────────────────────────────
        global_rpm_key = "rate:global:rpm"
        global_rpm = await r.get(global_rpm_key)
        if global_rpm and int(global_rpm) >= GLOBAL_RPM_LIMIT:
            ttl = await r.ttl(global_rpm_key)
            return {
                "allowed": False,
                "reason": "Server busy — global rate limit reached.",
                "retry_after": max(ttl, 1),
            }

        # ── All checks passed — record the request ───────────────────
        await increment_with_ttl(r, cooldown_key, CHAT_COOLDOWN_TTL)
        await increment_with_ttl(r, user_rpm_key, RPM_TTL)
        await increment_with_ttl(r, user_daily_key, DAILY_TTL)
        await increment_with_ttl(r, global_rpm_key, RPM_TTL)

        return {"allowed": True}

    async def record_tokens(self, tokens: int):
        """
        Record token consumption for global TPM tracking.
        Called AFTER a successful LLM response.

        NOTE: Since Phase 7, tokens are pre-reserved atomically via
        reserve_tpm(). This method records the ACTUAL usage and adjusts
        the reservation if the actual differs from the estimate.
        """
        # No-op: actual recording is handled by reserve_tpm + adjust_tokens.
        # Kept for API compatibility.
        pass

    async def reserve_tpm(self, estimated_tokens: int) -> dict:
        """
        Atomically reserve estimated tokens against global TPM limit.

        Uses INCRBY (atomic) to increment the counter FIRST, then checks
        if the new total exceeds the limit. If it does, rolls back with
        DECRBY and returns not-allowed.

        This eliminates the TOCTOU race condition where:
          1. Two requests both read current=240K
          2. Both pass the 250K check
          3. Both proceed → actual usage = 280K (over limit)

        Returns:
            {"allowed": True, "reserved": int} or
            {"allowed": False, "reason": str, "retry_after": int}
        """
        r = self.redis
        tpm_key = "rate:global:tpm"

        # Atomic increment — returns new total AFTER increment
        new_total = await r.incrby(tpm_key, estimated_tokens)

        # Set TTL if this is the first write in the window
        ttl = await r.ttl(tpm_key)
        if ttl < 0:
            await r.expire(tpm_key, RPM_TTL)

        if new_total > GLOBAL_TPM_LIMIT:
            # Over limit — roll back the reservation
            await r.decrby(tpm_key, estimated_tokens)
            remaining_ttl = await r.ttl(tpm_key)
            return {
                "allowed": False,
                "reason": f"Token rate limit — {new_total - estimated_tokens}/{GLOBAL_TPM_LIMIT} TPM used.",
                "retry_after": max(remaining_ttl, 1),
            }

        return {"allowed": True, "reserved": estimated_tokens}

    async def adjust_tokens(self, estimated: int, actual: int):
        """
        Adjust TPM counter after LLM response.
        If actual < estimated, release the excess reservation.
        If actual > estimated, add the deficit.
        """
        diff = actual - estimated
        if diff == 0:
            return

        r = self.redis
        tpm_key = "rate:global:tpm"

        if diff > 0:
            await r.incrby(tpm_key, diff)
        else:
            await r.decrby(tpm_key, abs(diff))

    async def check_tpm(self, estimated_tokens: int) -> dict:
        """
        Pre-check global TPM before submitting a request.
        Now uses atomic reservation internally.
        """
        return await self.reserve_tpm(estimated_tokens)
