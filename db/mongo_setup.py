# db/mongo_setup.py
# 
# Configures sharding strategy for the chat_database.
# Run this AFTER scripts/init_sharding.sh.

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from utils.config import MONGO_URI, DATABASE_NAME

async def setup_sharding():
    client = AsyncIOMotorClient(MONGO_URI)
    admin = client.admin
    db = client[DATABASE_NAME]

    print(f"Enabling sharding for database: {DATABASE_NAME}")
    try:
        await admin.command("enableSharding", DATABASE_NAME)
    except Exception as e:
        print(f"Database might already have sharding enabled: {e}")

    # Shard 'messages' collection on hashed 'chatId' (as seen in sh.status())
    print("Sharding 'messages' collection...")
    try:
        await admin.command({
            "shardCollection": f"{DATABASE_NAME}.messages",
            "key": {"chatId": "hashed"}
        })
    except Exception as e:
        print(f"Collection 'messages' might already be sharded: {e}")

    # Shard 'chats' collection on hashed 'userId'
    print("Sharding 'chats' collection...")
    try:
        await admin.command({
            "shardCollection": f"{DATABASE_NAME}.chats",
            "key": {"userId": "hashed"}
        })
    except Exception as e:
        print(f"Could not shard 'chats': {e}")

    print("Sharding configuration complete!")
    client.close()

if __name__ == "__main__":
    asyncio.run(setup_sharding())
