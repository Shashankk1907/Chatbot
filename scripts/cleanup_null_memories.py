import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

async def run():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["chatbot_db"]
    coll = db["user_memories"]
    
    result = await coll.delete_many({"$or": [{"entity": None}, {"entity": {"$exists": False}}]})
    print(f"Deleted {result.deleted_count} null/missing entity documents.")
    
if __name__ == "__main__":
    asyncio.run(run())
