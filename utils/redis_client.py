# utils/redis_client.py

import redis.asyncio as aioredis
from utils.config import REDIS_HOST, REDIS_PORT

# Singleton async Redis client
_redis = None


def get_redis_client():
    """
    Returns the async Redis client.
    Reuses a single connection pool across the application.
    """
    global _redis
    if _redis is None:
        _redis = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
        )
    return _redis