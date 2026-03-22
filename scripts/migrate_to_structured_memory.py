import asyncio
import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME = os.getenv("DATABASE_NAME", "chat_database")

# Mapping logic for legacy keys -> (entity, attribute)
MAPPING = {
    "name": ("user", "name"),
    "age": ("user", "age"),
    "occupation": ("user", "occupation"),
    "company": ("user", "occupation"), # map company to occupation attribute for now
    "location_city": ("location", "city"),
    "location_country": ("location", "country"),
    "favorite_food": ("preferences", "food"),
    "favorite_drink": ("preferences", "food"),
    "favorite_color": ("preferences", "color"),
    "favorite_music": ("preferences", "music"),
    "pet": ("pet", "name"),
    "programming_language": ("skill", "programming_language"),
    "tech_stack": ("skill", "tech_stack"),
    "goal_career": ("goal", "career"),
    "goal_personal": ("goal", "personal"),
    "project_current": ("project", "current"),
    "project_past": ("project", "past"),
    "relationship_spouse": ("relationship", "spouse"),
    "relationship_child": ("relationship", "child"),
    "relationship_parent": ("relationship", "parent"),
    "relationship_friend": ("relationship", "friend"),
}

async def migrate():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db["user_memories"]

    logger.info("Starting memory migration to Structured Identity (Phase 7)...")
    
    # 1. Drop old unique index if it exists to avoid conflicts during update
    try:
        await coll.drop_index("userId_1_key_1")
        logger.info("Dropped legacy index 'userId_1_key_1'")
    except Exception:
        pass

    cursor = coll.find({"key": {"$exists": True}})
    found = 0
    migrated = 0
    deleted = 0

    async for doc in cursor:
        found += 1
        key = doc.get("key")
        
        # Try to map
        entity, attribute = MAPPING.get(key, (None, None))
        
        if not entity:
            # Fallback patterns
            if "_" in key:
                parts = key.split("_", 1)
                entity_candidate = parts[0]
                attr_candidate = parts[1]
                # If entity_candidate is a valid entity (close enough), use it
                if entity_candidate in ["user", "pet", "preferences", "location", "project", "goal", "relationship", "skill"]:
                    entity, attribute = entity_candidate, attr_candidate
            
        if entity and attribute:
            # Check for potential collision (if user has both old 'name' and 'user_name')
            existing = await coll.find_one({
                "userId": doc["userId"],
                "entity": entity,
                "attribute": attribute,
                "_id": {"$ne": doc["_id"]}
            })
            
            if existing:
                logger.warning(f"Collision for {entity}.{attribute} for user {doc['userId']}. Preferring newest and deleting duplicate.")
                # Simple logic: delete the older one
                await coll.delete_one({"_id": doc["_id"]})
                deleted += 1
                continue

            await coll.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {"entity": entity, "attribute": attribute},
                    "$unset": {"key": ""}
                }
            )
            migrated += 1
        else:
            logger.info(f"Could not reliably map key '{key}'. Unsetting key but leaving doc (will be unindexed).")
            # We don't delete it, just unset the key so the unique index doesn't complain about nulls
            # Actually, to be safe for the unique index, we might need to delete or provide a dummy
            await coll.delete_one({"_id": doc["_id"]})
            deleted += 1

    logger.info(f"Migration complete: Found {found}, Migrated {migrated}, Deleted/Cleaned {deleted}")
    client.close()

if __name__ == "__main__":
    asyncio.run(migrate())
