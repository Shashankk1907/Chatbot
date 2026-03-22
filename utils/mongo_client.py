# utils/mongo_client.py

from motor.motor_asyncio import AsyncIOMotorClient
from utils.config import MONGO_URI, DATABASE_NAME

# Singleton async client
_client = None
_db = None


def get_mongo_db():
    """
    Returns the async MongoDB database instance.
    Reuses a single Motor client across the application.
    """
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(MONGO_URI)
        _db = _client[DATABASE_NAME]
    return _db


def get_mongo_client():
    """Returns the raw Motor client (for close operations etc.)."""
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
    return _client