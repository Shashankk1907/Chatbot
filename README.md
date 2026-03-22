# State Management and Data Flow Architecture

This document outlines the state management and data flow architecture of the Chatbot API system. The application employs a hybrid, multi-tiered memory and state management strategy using both **Redis** (for fast, ephemeral, and concurrent state) and **MongoDB** (as the persistent source of truth).

---

## 🏗️ 1. Core State Components

### Redis (Hot State & Concurrency)
Redis acts as the frontline for high-speed state operations, caching, and concurrency control. It handles:
- **Rate & Token Limiting:** Counters for Request Per Minute (RPM) and globally tracked Token Per Minute (TPM).
- **Concurrency Locks:** Distributed `SETNX` locks per chat (`lock:chat:{chat_id}`) to ensure only one message is processed at a time for a given chat, preventing race conditions.
- **Short-Term Memory Cache:** A sliding window of the 30 most recent messages stored in lists (`chat:{chat_id}:recent`).
- **Auth & Sessions:** Blacklisted access tokens and valid refresh tokens tracking.
- **Job Queues:** Async job queues for background workers (e.g., `queue:summarization`).
- **Pub/Sub (Realtime Events):** Event streams for pushing updates (titles, token updates, new messages) to connected SSE (Server-Sent Events) clients.

### MongoDB (Cold State & Source of Truth)
MongoDB ensures long-term persistence and structured querying. Key collections:
- **`users`**: User identity and hashed passwords.
- **`chats`**: Chat session metadata, including atomic counters (`messageCount`, `totalTokens`) and the overarching meta-summary.
- **`messages`**: Exhaustive log of all chat messages.
- **`summary_chunks`**: Mid-term memory preserving standalone summaries of older message batches.
- **`user_memories`**: Long-term explicit factual memories extracted for a user, enforcing a strict schema (entity -> attribute -> value).

---

## 🔄 2. Data Flow: The Chat Lifecycle

When a user sends a message, it flows through a strict 11-step orchestration loop (managed by `ChatOrchestrator`):

1. **Lock Acquisition:** A Redis lock is acquired for the `chat_id`. If busy, the system returns a `423 Locked`.
2. **Threshold Checks:** Redis rate limits and token limits are validated.
3. **Context Assembly (MemoryManager):** 
   - Attempts to load the context entirely from the Redis hot cache.
   - If missing, it falls back to MongoDB, reconstructing the session and re-warming the Redis cache.
   - It injects chunk summaries (mid-term memory) at the top of the context so the LLM doesn't lose track of older parts of the conversation.
4. **Long-Term Memory Injection (UserMemoryExtractor):**
   - The system performs a semantic search in MongoDB (`user_memories`) against the current user message and injects relevant factual constraints securely into the LLM system prompt.
5. **LLM Generation:** The constructed context is passed to the LLM via `ModelRouter`, equipped with retry mechanisms and exponential backoff.
6. **Data Persistence:**
   - The User message and Assistant response are appended to MongoDB (`messages`).
   - Atomic `$inc` operations update the `messageCount` and `totalTokens` in the `chats` collection.
   - Both messages are pushed into the Redis sliding window cache.
7. **Background Triggers:**
   - **Summarization:** If `totalTokens > 10,000` or `messageCount > 80`, a job is appended to the Redis summarization queue.
   - **Auto-naming:** Triggered asynchronously if it's the first message of a "New Chat".
   - **Memory Extraction:** A regex-based signal detector checks if the user shared personal facts. If triggered, an async memory extraction job spins up.
8. **Lock Release:** The Redis chat lock is released.

---

## 🧠 3. Multi-Tiered Memory Mechanics

### A. Short-Term Memory (Context Window)
Maintained by the `MemoryManager`. It relies on a fast Redis tail-fetch of the recent messages to feed immediately into the token-budget builder, meaning typical interactions require 0 database reads.

### B. Mid-Term Memory (Summarization Worker)
An asynchronous background process (`services/summarization_worker.py`):
1. **Dequeues** jobs from Redis.
2. Extracts the oldest ~50% of the active context.
3. Requests a dense `chunk_summary` from the LLM, strictly storing it in `summary_chunks`.
4. **Soft-deletes** the original messages from the database to save space, and decrements chat stats.
5. **Meta-Summary:** Every 5 chunks, the worker automatically merges them into a high-level `meta-summary` stored on the `chats` document.

### C. Long-Term Memory (User Memory Extractor)
Dedicated to extracting durable facts (e.g., "I live in Berlin", "My dog's name is Rex").
- **Trigger:** Avoids naive polling. Primarily triggered by cheap Regex pattern matching ("I am a", "I live in") on incoming messages.
- **Sanitization:** LLM outputs are piped through `json-repair` and strictly validated using **Pydantic schemas** to ensure only approved Entities and Attributes touch the DB.
- **Storage:** Upserted into `user_memories` with an embedding vector for fast semantic retrieval during future conversations.

---

## 🛡️ 4. Concurrency & Integrity Measures

- **Atomic Increments:** Modifying chat tokens and message counts is done using MongoDB `$inc` to prevent race conditions during parallel processing and worker cleanup.
- **Idempotency Locks:** Background workers use separate Redis limits (e.g., `summarization_lock:{chatId}`) to ensure only one worker processes a specific chat's cleanup simultaneously.
- **Debounce / Throttling:** The Long-Term memory extractor utilizes a 60-second Redis debounce lock (`cooldown:memory_extract:{userId}`) to prevent quota-draining LLM call spikes.
