
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

import uuid
from pydantic import BaseModel

from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from utils.mongo_client import get_mongo_db, get_mongo_client
from utils.redis_client import get_redis_client
from utils.security import (
    create_access_token, decode_access_token, 
    create_refresh_token, decode_refresh_token,
    hash_password, verify_password
)
from db.redis_helpers import (
    store_refresh_token, validate_refresh_token, delete_refresh_token,
    blacklist_access_token, is_token_blacklisted
)
from db.mongo_helpers import (
    create_chat, get_user_chats, get_last_messages,
    create_user, get_user_by_email,
    is_chat_owner,
)
from db.mongo_helpers import get_chat_by_id, update_chat_title
from services.chat_orchestrator import ChatOrchestrator
from services.summarization_worker import run_worker as run_summarization_worker
from services.memory_worker import run_worker as run_memory_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App state (set during lifespan) ───────────────────────────────────────
orchestrator: ChatOrchestrator | None = None
worker_tasks: list[asyncio.Task] = []
shutdown_event: asyncio.Event | None = None


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
      1. Initialize Mongo + Redis singleton clients
      2. Create ChatOrchestrator (owns MemoryManager, TokenManager, etc.)
      3. Start the background summarization worker
    Shutdown:
      1. Signal worker to stop gracefully
      2. Wait for worker to finish current job
      3. Close Redis + Mongo connections
    """
    global orchestrator, worker_tasks, shutdown_event

    logger.info("🚀 Starting up...")

    # 1. Initialize DB clients
    db = get_mongo_db()
    redis = get_redis_client()

    # Quick connectivity test
    try:
        await redis.ping()
        logger.info("  ✅ Redis connected")
    except Exception as e:
        logger.warning(f"  ⚠️  Redis connection failed: {e}")

    # 1.5 Setup MongoDB Indexes
    try:
        from pymongo import ASCENDING, DESCENDING

        # ── user_memories ─────────────────────────────────────────────
        mm_coll = db["user_memories"]

        # Step 1: Drop any existing (possibly partial or conflicting) userId_1_key_1 index
        mm_indexes = await mm_coll.list_indexes().to_list(length=100)
        existing_names = {idx["name"] for idx in mm_indexes}
        if "userId_1_key_1" in existing_names:
            logger.info("  🗑️  Dropping existing 'userId_1_key_1' index before rebuild")
            await mm_coll.drop_index("userId_1_key_1")

        # Step 2: Removed Legacy Deduplication - Handled via Phase 7 structural constraints

        # Step 3: Create the unique index now that duplicates are gone
        # Phase 7: Structured Identity Unique Index
        if "userId_1_entity_1_attribute_1" not in existing_names:
            logger.info("  🔧 Creating new 'userId_1_entity_1_attribute_1' unique index")
            await mm_coll.create_index(
                [("userId", ASCENDING), ("entity", ASCENDING), ("attribute", ASCENDING)],
                unique=True,
                name="userId_1_entity_1_attribute_1",
            )
        
        # Clean up legacy index if it exists
        if "userId_1_key_1" in existing_names:
            logger.info("  🗑️  Dropping legacy 'userId_1_key_1' index")
            await mm_coll.drop_index("userId_1_key_1")

        await mm_coll.create_index(
            [("userId", ASCENDING), ("importance", DESCENDING)],
            name="userId_1_importance_-1",
        )
        await mm_coll.create_index(
            [("lastAccessed", ASCENDING)],
            name="lastAccessed_1",
        )

        # ── messages: compound index for get_last_messages() speed ────
        await db["messages"].create_index(
            [("chatId", ASCENDING), ("isSummarized", ASCENDING), ("createdAt", DESCENDING)],
            name="messages_context_lookup",
        )

        # ── summary_chunks: ordered lookup by chat + chunk index ──────
        await db["summary_chunks"].create_index(
            [("chatId", ASCENDING), ("chunkIndex", ASCENDING)],
            unique=True,
            name="summary_chunks_ordered",
        )

        logger.info("  ✅ MongoDB indexes ensured")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create Mongo indexes: {e}")

    # 2. Create orchestrator
    orchestrator = ChatOrchestrator(db, redis)
    logger.info("  ✅ ChatOrchestrator initialized")

    # 3. Start background workers
    shutdown_event = asyncio.Event()
    worker_tasks = [
        asyncio.create_task(run_summarization_worker(db, redis, shutdown_event=shutdown_event)),
        asyncio.create_task(run_memory_worker(db, redis, shutdown_event=shutdown_event))
    ]
    logger.info("🟢 App ready\n")

    yield  # ── App runs here ──

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("🔴 Shutting down...")

    # Signal worker to stop
    if shutdown_event:
        shutdown_event.set()
    if worker_tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*worker_tasks, return_exceptions=True), timeout=10)
            logger.info("  ✅ Workers stopped gracefully")
        except asyncio.TimeoutError:
            for t in worker_tasks:
                t.cancel()
            logger.warning("  ⚠️  Workers cancelled (timeout)")

    # Close Redis
    try:
        await redis.close()
        logger.info("  ✅ Redis closed")
    except Exception:
        pass

    # Close Mongo
    try:
        get_mongo_client().close()
        logger.info("  ✅ MongoDB closed")
    except Exception:
        pass

    logger.info("👋 Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Chatbot API",
    description="AI chatbot with hybrid memory, async summarization, and model routing",
    version="2.0.0",
    lifespan=lifespan,
)

# serve user-uploaded attachments from the filesystem
from fastapi.staticfiles import StaticFiles
UPLOAD_DIR = "uploads"
import os
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite default
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired access token")
    
    jti = payload.get("jti")
    if jti:
        redis = get_redis_client()
        if await is_token_blacklisted(redis, jti):
            raise HTTPException(status_code=401, detail="Token has been revoked")
            
    return payload


# ── Request / Response models ─────────────────────────────────────────────

class CreateChatRequest(BaseModel):
    title: str = "New Chat"

class ChatMessageRequest(BaseModel):
    chat_id: str | None = None
    message_text: str
    persona: str = "default"

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str


# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Health check / API info."""
    return {"status": "ok", "version": "2.0.0", "frontend": "streamlit_app.py"}



@app.get("/test")
async def test_route():
    """Health check."""
    return {"status": "ok", "version": "2.0.0"}


# ── Auth Endpoints ────────────────────────────────────────────────────────

@app.post("/auth/register")
async def register_local(req: RegisterRequest):
    """Register a new user with email/password."""
    db = get_mongo_db()
    existing_user = await get_user_by_email(db, req.email)
    
    if existing_user:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="User with this email already exists")
        
    pwd_hash = hash_password(req.password)
    user_id = await create_user(db, email=req.email, password_hash=pwd_hash)
    
    access_token, _ = create_access_token({"sub": str(user_id), "email": req.email})
    refresh_token, ref_jti, ref_exp = create_refresh_token({"sub": str(user_id)})
    
    redis = get_redis_client()
    from datetime import datetime, timezone
    expires_in = int((ref_exp - datetime.now(timezone.utc)).total_seconds())
    await store_refresh_token(redis, str(user_id), ref_jti, expires_in)
    
    return {"status": "ok", "access_token": access_token, "refresh_token": refresh_token, "user_id": str(user_id)}

@app.post("/auth/login")
async def login_local(req: LoginRequest):
    """Local email/password login."""
    db = get_mongo_db()
    user = await get_user_by_email(db, req.email)
    
    if not user or not user.get("passwordHash"):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    if not verify_password(req.password, user["passwordHash"]):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    user_id_str = str(user["_id"])
    access_token, _ = create_access_token({"sub": user_id_str, "email": user["email"]})
    refresh_token, ref_jti, ref_exp = create_refresh_token({"sub": user_id_str})
    
    redis = get_redis_client()
    from datetime import datetime, timezone
    expires_in = int((ref_exp - datetime.now(timezone.utc)).total_seconds())
    await store_refresh_token(redis, user_id_str, ref_jti, expires_in)
    
    return {"status": "ok", "access_token": access_token, "refresh_token": refresh_token, "user_id": user_id_str}

@app.post("/auth/refresh")
async def refresh_endpoint(req: RefreshRequest):
    """Exchange a valid refresh token for a new access+refresh pair."""
    payload = decode_refresh_token(req.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        
    user_id = payload.get("sub")
    jti = payload.get("jti")
    
    redis = get_redis_client()
    is_valid = await validate_refresh_token(redis, user_id, jti)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Refresh token revoked or not found")
        
    await delete_refresh_token(redis, user_id, jti)
    
    db = get_mongo_db()
    from bson import ObjectId
    user = await db["users"].find_one({"_id": ObjectId(user_id)})
    email = user["email"] if user else ""
    
    new_access, _ = create_access_token({"sub": user_id, "email": email})
    new_refresh, new_ref_jti, ref_exp = create_refresh_token({"sub": user_id})
    
    from datetime import datetime, timezone
    expires_in = int((ref_exp - datetime.now(timezone.utc)).total_seconds())
    await store_refresh_token(redis, user_id, new_ref_jti, expires_in)
    
    return {"status": "ok", "access_token": new_access, "refresh_token": new_refresh}

@app.post("/auth/logout")
async def logout_endpoint(req: LogoutRequest, current_user: dict = Depends(get_current_user)):
    """Logout by revoking the refresh token and blacklisting the access token."""
    redis = get_redis_client()
    
    access_jti = current_user.get("jti")
    access_exp = current_user.get("exp")
    if access_jti and access_exp:
        import time
        expires_in = int(access_exp - time.time())
        if expires_in > 0:
            await blacklist_access_token(redis, access_jti, expires_in)
            
    ref_payload = decode_refresh_token(req.refresh_token)
    if ref_payload:
        user_id = ref_payload.get("sub")
        ref_jti = ref_payload.get("jti")
        if user_id == current_user.get("sub"):
            await delete_refresh_token(redis, user_id, ref_jti)
            
    return {"status": "ok", "message": "Successfully logged out"}


@app.get("/auth/google")
async def google_login():
    """Placeholder for Google OAuth redirect."""
    return {"status": "todo", "message": "Redirect to Google OAuth"}


@app.get("/auth/github")
async def github_login():
    """Placeholder for GitHub OAuth redirect."""
    return {"status": "todo", "message": "Redirect to GitHub OAuth"}


@app.post("/chat/create")
async def create_chat_endpoint(
    req: CreateChatRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new chat session for the authenticated user.
    """
    db = get_mongo_db()
    try:
        user_oid = current_user["sub"]
        created_oid = await create_chat(db, user_oid, req.title)
        return {"status": "ok", "chat_id": str(created_oid)}
    except Exception as e:
        logger.error(f"Failed to create chat: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})


@app.post("/chat")
async def chat_endpoint(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Send a message in a chat session.
    Derives user identity from JWT to prevent ID impersonation.
    """
    if orchestrator is None:
        return JSONResponse(status_code=503, content={"status": "error", "reason": "Service not ready"})

    user_oid = current_user["sub"]
    db = get_mongo_db()

    # parse incoming data
    attachments_meta = []
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        chat_id = form.get("chat_id")
        message_text = form.get("message_text", "")
        persona = form.get("persona", "default")
        
        # collect Files
        files = []
        for key, val in form.multi_items():
            if key == "attachments" and hasattr(val, "filename"):
                files.append(val)
        
        for f in files:
            filename = f.filename or "unnamed"
            save_name = f"{uuid.uuid4().hex}_{filename}"
            save_path = os.path.join(UPLOAD_DIR, save_name)
            with open(save_path, "wb") as out_file:
                content = await f.read()
                out_file.write(content)
            attachments_meta.append({
                "filename": filename,
                "url": f"/uploads/{save_name}",
                "content_type": f.content_type,
            })
    else:
        json_data = await request.json()
        chat_id = json_data.get("chat_id")
        message_text = json_data.get("message_text", "")
        persona = json_data.get("persona", "default")

    # Ownership Check & Chat Creation
    if chat_id:
        if not await is_chat_owner(db, chat_id, user_oid):
            return JSONResponse(status_code=403, content={"status": "error", "reason": "Access denied to this chat"})
    else:
        # Create chat on first message
        created_oid = await create_chat(db, user_oid, "New Chat")
        chat_id = str(created_oid)

    result = await orchestrator.handle_message(
        user_oid, chat_id, message_text, persona=persona, attachments=attachments_meta
    )

    if result.get("status") == "error":
        code = result.get("code", 500)
        return JSONResponse(status_code=code, content=result)

    result["chat_id"] = chat_id
    return result


@app.get("/chats")
async def list_chats(
    limit: int = 10, 
    skip: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """List chat sessions for the authenticated user with pagination."""
    db = get_mongo_db()
    try:
        user_oid = current_user["sub"]
        chats = await get_user_chats(db, user_oid, limit=limit, skip=skip)
        return {
            "status": "ok",
            "chats": [
                {
                    "chat_id": str(c["_id"]),
                    "title": c.get("title", "Untitled"),
                    "message_count": c.get("messageCount", 0),
                    "total_tokens": c.get("totalTokens", 0),
                    "last_active": c.get("lastActive").isoformat()
                        if c.get("lastActive") else None,
                    "has_summary": c.get("summary") is not None,
                }
                for c in chats
            ],
        }
    except Exception as e:
        logger.error(f"Failed to list chats: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})


@app.get("/chat/{chat_id}/messages")
async def get_chat_messages(
    chat_id: str, 
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Get messages for a chat session. Validates ownership."""
    db = get_mongo_db()
    user_oid = current_user["sub"]
    
    # Ownership Check
    if not await is_chat_owner(db, chat_id, user_oid):
        return JSONResponse(status_code=403, content={"status": "error", "reason": "Access denied to this chat"})

    try:
        messages = await get_last_messages(db, chat_id, limit=limit, include_summarized=True)
        return {
            "status": "ok",
            "messages": [
                {
                    "id": str(m.get("_id")),
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "tokens": m.get("tokens", 0),
                    "created_at": m.get("createdAt").isoformat()
                        if m.get("createdAt") else None,
                    "attachments": m.get("attachments", []),
                }
                for m in messages
            ],
        }
    except Exception as e:
        logger.error(f"Failed to get messages: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})


# ── Debug / Observability endpoints ──────────────────────────────────────

@app.get("/debug/redis/{chat_id}")
async def debug_redis(
    chat_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Live Redis state for a chat. Restricted to chat owner."""
    db = get_mongo_db()
    user_oid = current_user["sub"]
    if not await is_chat_owner(db, chat_id, user_oid):
        return JSONResponse(status_code=403, content={"status": "error", "reason": "Access denied"})

    redis = get_redis_client()
    try:
        # Cached recent messages
        raw_msgs = await redis.lrange(f"chat:{chat_id}:recent", 0, -1)
        import json as _json
        cached_messages = []
        for raw in raw_msgs:
            try:
                cached_messages.append(_json.loads(raw))
            except Exception:
                cached_messages.append(raw)

        # Lock status
        lock_val = await redis.get(f"lock:chat:{chat_id}")
        lock_ttl = await redis.ttl(f"lock:chat:{chat_id}")

        # Cooldown
        cooldown = await redis.get(f"rate:chat:{chat_id}:cooldown")

        # Global rate limits
        global_rpm = await redis.get("rate:global:rpm")

        # Summarization queue depth
        queue_len = await redis.llen("queue:summarization")

        return {
            "status": "ok",
            "chat_id": chat_id,
            "cached_messages": cached_messages,
            "cached_count": len(cached_messages),
            "lock": {"held": lock_val is not None, "ttl": lock_ttl},
            "cooldown": cooldown,
            "global_rpm": global_rpm,
            "summarization_queue_depth": queue_len,
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


@app.get("/debug/mongo/{chat_id}")
async def debug_mongo(
    chat_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Live MongoDB state for a chat. Restricted to chat owner."""
    db = get_mongo_db()
    user_oid = current_user["sub"]
    if not await is_chat_owner(db, chat_id, user_oid):
        return JSONResponse(status_code=403, content={"status": "error", "reason": "Access denied"})

    try:
        # Chat document
        chat_doc = await db["chats"].find_one({"_id": _OID(chat_id)})
        chat_info = None
        if chat_doc:
            chat_info = {
                "chat_id": str(chat_doc["_id"]),
                "user_id": str(chat_doc.get("userId", "")),
                "title": chat_doc.get("title", "Untitled"),
                "message_count": chat_doc.get("messageCount", 0),
                "total_tokens": chat_doc.get("totalTokens", 0),
                "summary": chat_doc.get("summary"),
                "summary_tokens": chat_doc.get("summaryTokens", 0),
                "created_at": chat_doc.get("createdAt", ""),
                "last_active": chat_doc.get("lastActive", ""),
            }
            # Convert datetimes to ISO strings
            for k in ("created_at", "last_active"):
                v = chat_info.get(k)
                if hasattr(v, "isoformat"):
                    chat_info[k] = v.isoformat()

        # Recent messages
        cursor = db["messages"].find(
            {"chatId": _OID(chat_id)},
        ).sort("createdAt", -1).limit(20)
        msgs_raw = await cursor.to_list(length=20)
        messages = []
        for m in msgs_raw:
            msg = {
                "id": str(m["_id"]),
                "role": m.get("role", ""),
                "content": m.get("content", "")[:200],
                "tokens": m.get("tokens", 0),
                "created_at": m.get("createdAt", ""),
            }
            if hasattr(msg["created_at"], "isoformat"):
                msg["created_at"] = msg["created_at"].isoformat()
            messages.append(msg)

        return {
            "status": "ok",
            "chat": chat_info,
            "recent_messages": list(reversed(messages)),
            "message_count_in_db": len(messages),
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


@app.post("/admin/cleanup-empty-chats")
async def cleanup_empty_chats(current_user: dict = Depends(get_current_user)):
    """Delete chats with zero messages. Restricted to authenticated users."""
    # Note: In a real app, this should be restricted to admin roles.
    # For now, we at least ensure the user is logged in.
    db = get_mongo_db()
    try:
        result = await db["chats"].delete_many({"messageCount": 0})
        return {"status": "ok", "deleted_count": result.deleted_count}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})


# ── Server-Sent Events (SSE) for realtime updates ─────────────────────────

@app.get("/events")
async def sse_events(
    request: Request,
    chat_id: str | None = None,
    token: str | None = None,          # EventSource cannot send headers — token goes here
):
    """
    SSE endpoint subscribing to Redis channels.

    Browser EventSource cannot send custom headers (Authorization: Bearer).
    Token is accepted as a ?token= query parameter for SSE connections,
    which is safe here because SSE connections are same-origin and the token
    is short-lived (see JWT_SECRET_KEY).

    Falls back to Authorization header for non-browser clients.
    """
    # Resolve token: query param first (EventSource), then header (programmatic clients)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return JSONResponse(status_code=401, content={"status": "error", "reason": "Authentication required"})

    payload = decode_access_token(token)
    if not payload:
        return JSONResponse(status_code=401, content={"status": "error", "reason": "Invalid or expired token"})

    redis = get_redis_client()
    jti = payload.get("jti")
    if jti and await is_token_blacklisted(redis, jti):
        return JSONResponse(status_code=401, content={"status": "error", "reason": "Token has been revoked"})

    user_oid = payload["sub"]
    db = get_mongo_db()
    redis = get_redis_client()
    pubsub = redis.pubsub()
    channels = []

    if chat_id:
        # Validate ownership of the chat
        if not await is_chat_owner(db, chat_id, user_oid):
            return JSONResponse(status_code=403, content={"status": "error", "reason": "Access denied"})
        channels.append(f"chat:{chat_id}:events")

    # Always subscribe to user-specific events
    channels.append(f"user:{user_oid}:events")

    await pubsub.subscribe(*channels)

    async def event_generator():
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
                if msg and msg.get("type") == "message":
                    data = msg.get("data")
                    if isinstance(data, bytes):
                        try:
                            payload_str = data.decode()
                        except Exception:
                            payload_str = str(data)
                    else:
                        payload_str = str(data)
                    yield f"data: {payload_str}\n\n"
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(0.01)
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            except Exception:
                pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)