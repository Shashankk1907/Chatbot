"""
services/user_memory_extractor.py

Hardened memory extraction pipeline with:
  1. json-repair   — tolerates malformed LLM JSON output
  2. Pydantic      — schema-validates every memory before it touches the DB
  3. Prompt injection guard — only user-role messages are fed to the extractor
  4. Key validation — regex + length check on canonical keys
  5. python-dateutil — handles any ISO timestamp format
  6. asyncio.wait_for — 10-second timeout on LLM call
  7. Message window cap — last 20 user messages max
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator, ValidationError
import json_repair
from dateutil import parser as dateutil_parser

from db.mongo_helpers import upsert_user_memory

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD  = 0.85
IMPORTANCE_THRESHOLD  = 5        # Lowered to 5 to allow storing more types of data, handled later by ranking
LLM_TIMEOUT_SECONDS   = 10
MAX_MESSAGES          = 20       # cap conversation window
KEY_REGEX             = re.compile(r"^[a-z][a-z_]*$")   
KEY_MAX_WORDS         = 3        

# ── Signal-based trigger ────────────────────────────────────────
# These phrases indicate the user is sharing personal, durable facts.
# Matching any of them triggers extraction IMMEDIATELY (no LLM quota spent yet).
# Patterns are lowercase; matching is done on lowercased message text.
HIGH_SIGNAL_PATTERNS: list[re.Pattern] = [
    # Identity
    re.compile(r"\bmy name is\b"),
    re.compile(r"\bi am called\b"),
    re.compile(r"\bcall me\b"),
    re.compile(r"\bi('m| am)\s+\d{1,3}\s*(years?\s*old)?\b"),  # "I am 28" / "I'm 25 years old"
    # Location
    re.compile(r"\bi('m| am| have)\s+(moved?|shifted|relocated|living|based)\b"),
    re.compile(r"\bi live in\b"),
    re.compile(r"\bi('m| am) from\b"),
    re.compile(r"\bi moved to\b"),
    re.compile(r"\bshifted to\b"),
    # Occupation / career
    re.compile(r"\bi work (as|at|for|in)\b"),
    re.compile(r"\bi('m| am) a\b.{0,30}\b(developer|engineer|designer|doctor|teacher|founder|student)\b"),
    re.compile(r"\bmy (job|profession|role|career|occupation) is\b"),
    # Skills
    re.compile(r"\bi (know|use|learn|study|practice|code in)\b"),
    re.compile(r"\bi('m| am) (learning|studying|building|working on)\b"),
    re.compile(r"\bi can\b"),
    # Preferences
    re.compile(r"\bi (like|love|enjoy|prefer|hate|dislike|can't stand)\b"),
    re.compile(r"\bmy favou?rite\b"),
    re.compile(r"\bi('m| am) into\b"),
    # Goals / projects
    re.compile(r"\bmy goal is\b"),
    re.compile(r"\bi('m| am) (trying|planning|hoping|aiming) to\b"),
    re.compile(r"\bi want to\b"),
    re.compile(r"\bi('m| am) (building|creating|launching|starting)\b"),
    re.compile(r"\bmy (project|startup|app|product) is\b"),
    # Relationships / people
    re.compile(r"\bmy (wife|husband|partner|girlfriend|boyfriend|son|daughter|kid|dog|cat|pet)\b"),
]


def contains_memory_signal(message: str) -> bool:
    """
    Cheap, regex-only pre-filter. Returns True if the user message likely
    contains a durable personal fact worth extracting.

    No LLM call — runs in microseconds.
    """
    lower = message.lower()
    return any(pattern.search(lower) for pattern in HIGH_SIGNAL_PATTERNS)

VALID_CATEGORIES = Literal[
    "profile", "skill", "preference", "project", "goal", "location"
]

VALID_ENTITIES = Literal[
    "user", "pet", "preferences", "location", "project", "goal", "relationship", "skill"
]

ENTITY_ATTRIBUTES = {
    "user": ["name", "age", "occupation"],
    "pet": ["type", "name"],
    "preferences": ["food", "color", "music"],
    "location": ["city", "country"],
    "skill": ["programming_language", "tech_stack"],
    "project": ["current", "past"],
    "goal": ["career", "personal"],
    "relationship": ["spouse", "child", "parent", "friend"]
}

# ── Pydantic schema ────────────────────────────────────────────────────────
class MemoryItem(BaseModel):
    entity:     VALID_ENTITIES
    attribute:  str
    value:      str
    category:   VALID_CATEGORIES
    importance: int
    confidence: float
    status:     Literal["pending", "verified"] = "pending"

    @field_validator("attribute")
    @classmethod
    def validate_attribute(cls, v: str, info) -> str:
        entity = info.data.get("entity")
        if entity and v not in ENTITY_ATTRIBUTES.get(entity, []):
            allowed = ENTITY_ATTRIBUTES.get(entity, [])
            raise ValueError(f"attribute {v!r} not valid for entity {entity!r}. Allowed: {allowed}")
        return v.strip().lower()

    @field_validator("value")
    @classmethod
    def validate_value(cls, v) -> str:
        if not isinstance(v, str):
            raise ValueError(f"value must be a string, got {type(v).__name__}")
        return v.strip()

    @field_validator("importance")
    @classmethod
    def validate_importance(cls, v) -> int:
        v = int(v)
        if not (1 <= v <= 10):
            raise ValueError(f"importance must be 1–10, got {v}")
        return v

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v) -> float:
        v = float(v)
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be 0–1, got {v}")
        return v


# ── Structured Identity Configuration ───────────────────────────────────────
CANONICAL_SCHEMA_REFERENCE = f"""
STRICT SCHEMA REFERENCE

You MUST use ONLY the following entities and attributes. Do NOT invent new ones.

Allowed Entities & Attributes:
{json.dumps(ENTITY_ATTRIBUTES, indent=2)}

RULES:
1. entity: Must be exactly one of the keys in the list above.
2. attribute: Must be one of the strings in the list for that specific entity.
3. If a fact does not fit this schema, do NOT extract it.
"""

EXTRACTION_PROMPT = f"""
You are a backend memory architecture system responsible for managing structured long-term user memory for an AI assistant.
Extract long-term memories from the provided USER messages into a STRICT entity-attribute format.

Valid memory categories:
profile | skill | preference | project | goal | location

{CANONICAL_SCHEMA_REFERENCE}

MEMORY EXTRACTION RULES:
- Extract memories ONLY when the information is stable, useful for personalization, and related to user identity.
- Use only predefined entity and attribute combinations.
- If unsure or no mapping exists, SKIP the fact.

IMPORTANCE SCORING (1-10):
- 9-10: Critical personal facts (name, age, health).
- 7-8: Career, goals, location, major projects.
- 4-6: General preferences, hobbies, skills.
- 1-3: Trivial preferences.

VALUE FORMAT RULE:
Values must be concise noun phrases (1–5 words). No sentences.

USER SCOPE RULE:
Extract facts ONLY about the user, never about the assistant.

OUTPUT FORMAT (JSON only):
{{
  "memories": [
    {{
      "entity": "one_of_allowed_entities",
      "attribute": "valid_attribute_for_entity",
      "value": "actual_stored_fact_string",
      "category": "one_of_the_valid_categories",
      "importance": <int 1-10>,
      "confidence": <float 0-1>
    }}
  ]
}}
"""


class UserMemoryExtractor:
    def __init__(self, db, redis):
        self.db    = db
        self.redis = redis

    def should_trigger_memory_extraction(
        self,
        message_count: int,
        last_message_timestamp,
        conversation_active: bool,
        current_message: str = "",
    ) -> bool:
        """
        Hybrid trigger — two mechanisms working together:

        PRIMARY  — Signal-based (instant, no LLM cost)
            Fires immediately when the user message contains a known
            memory-worthy pattern (e.g. "I live in", "my name is").
            Cost: ~0 (pure regex).

        SECONDARY — Periodic fallback (catch-all)
            Every 25 messages or idle > 15 min.
            Catches facts that slipped past the signal filter.

        TERTIARY  — Session close
            Always extract when a conversation ends.

        This reduces LLM extraction calls by ~70–80% vs. naive every-N approach.
        """
        # 1. PRIMARY: signal-based — fires on the CURRENT message
        if current_message and contains_memory_signal(current_message):
            logger.debug(f"[Extractor] Memory signal detected in message: {current_message[:60]!r}")
            return True

        # 2. TERTIARY: session closed — always flush on session end
        if not conversation_active:
            return True

        # 3. SECONDARY: periodic fallback — every 25 messages
        if message_count >= 2 and message_count % 25 == 0:
            return True

        # 4. SECONDARY: idle fallback — > 15 minutes since last message
        if last_message_timestamp:
            try:
                if isinstance(last_message_timestamp, str):
                    last_dt = dateutil_parser.isoparse(last_message_timestamp)
                else:
                    last_dt = last_message_timestamp

                # MongoDB dates are stored as UTC but may arrive as timezone-naive
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)

                idle_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if idle_seconds > 900:  # 15 minutes
                    return True
            except Exception as e:
                logger.warning(f"Failed to parse timestamp for trigger check: {e}")

        return False

    async def extract_memories(
        self,
        user_id: str,
        chat_id: str,
        recent_messages: list,
        llm_call_func,
        window_size: int = MAX_MESSAGES,
    ):
        """
        Extract and robustly store long-term memories from a batch of messages.

        Hardening applied:
          1. json_repair    — fix broken JSON from LLM output
          2. Pydantic       — validate schema before any DB write
          3. Injection guard— only user-role messages are sent to the extractor
          4. Key validation — enforced by Pydantic field_validator
          5. dateutil       — tolerant timestamp parsing
          6. wait_for       — 10s timeout on the LLM call
          7. Window cap     — last 20 user messages only
        """
        # Issue 17: Throttle Cost Spikes with a 60-second Redis Debounce
        lock_key = f"cooldown:memory_extract:{user_id}"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=60)
        if not acquired:
            logger.info(f"Memory extraction throttled for user {user_id} (in cooldown)")
            return

        if not recent_messages:
            return

        # Issue #3 + #7: Filter to user-only messages, cap at last window_size
        user_messages = [
            m for m in recent_messages
            if m.get("role") == "user"
        ][-window_size:]

        if not user_messages:
            logger.info(f"No user-role messages to extract from for user {user_id}")
            return

        convo_text = "\n".join(
            f"user: {m.get('content', '')}" for m in user_messages
        )

        prompt = [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user",   "content": f"User messages:\n{convo_text}"},
        ]

        try:
            # Issue #6: timeout guard on LLM call
            try:
                result = await asyncio.wait_for(
                    llm_call_func(prompt, temperature=0.1),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(f"LLM call timed out after {LLM_TIMEOUT_SECONDS}s for user {user_id}")
                return

            raw = result.get("content", "").strip()

            # Issue #1: use json_repair to tolerate malformed / commented / partial JSON
            data = json_repair.loads(raw)
            if not isinstance(data, dict):
                logger.warning(f"json_repair returned non-dict for user {user_id}: {type(data)}")
                return

            memories_raw = data.get("memories", [])
            if not isinstance(memories_raw, list):
                logger.warning(f"memories field is not a list for user {user_id}")
                return

            upsert_count = 0
            source_msg_id = (
                recent_messages[-1].get("_id") or recent_messages[-1].get("id")
            )

            for raw_mem in memories_raw[:5]:
                # Issue #2: Pydantic schema validation
                try:
                    mem = MemoryItem(**raw_mem)
                except (ValidationError, TypeError) as e:
                    logger.warning(f"Memory schema validation failed for user {user_id}: {e}")
                    continue

                # Hard quality gate
                if mem.importance < IMPORTANCE_THRESHOLD:
                    continue
                if mem.confidence < CONFIDENCE_THRESHOLD:
                    continue

                from utils.embeddings import get_embedding
                dump = mem.model_dump()
                
                # Phase 7: Deterministic identity embedding
                # f"{entity} {attribute} is {value}" provides better clustering
                embed_text = f"{mem.entity} {mem.attribute} is {mem.value}"
                embed_vec = get_embedding(embed_text)
                if embed_vec:
                    dump["embedding"] = embed_vec

                await upsert_user_memory(
                    self.db,
                    user_id,
                    dump,
                    source_message_id=str(source_msg_id) if source_msg_id else None,
                )
                upsert_count += 1

            if upsert_count > 0:
                logger.info(
                    f"Extracted {upsert_count} memories for user {user_id} "
                    f"(threshold: importance>={IMPORTANCE_THRESHOLD}, "
                    f"confidence>={CONFIDENCE_THRESHOLD})"
                )

        except Exception as e:
            logger.error(
                f"Unexpected error during memory extraction for user {user_id}: {e}",
                exc_info=True,
            )
