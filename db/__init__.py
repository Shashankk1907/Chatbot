# db/__init__.py
#
# Database Architecture Layer
# Exports all async helpers for MongoDB and Redis.

from db.mongo_helpers import (
    create_user,
    get_user_by_email,
    create_chat,
    get_user_chats,
    insert_message,
    increment_chat_stats,
    increment_user_token_stats,
    get_last_messages,
    update_chat_summary,
)

from db.redis_helpers import (
    set_session,
    get_session,
    delete_session,
    push_recent_message,
    get_recent_messages,
    clear_recent_messages,
    increment_with_ttl,
    check_rate_limit,
    track_tokens_user,
    track_tokens_chat,
    get_user_daily_tokens,
    acquire_chat_lock,
    release_chat_lock,
    enqueue_summarization_job,
    dequeue_summarization_job,
)

__all__ = [
    # Mongo
    "create_user",
    "get_user_by_email",
    "create_chat",
    "get_user_chats",
    "insert_message",
    "increment_chat_stats",
    "increment_user_token_stats",
    "get_last_messages",
    "update_chat_summary",
    # Redis
    "set_session",
    "get_session",
    "delete_session",
    "push_recent_message",
    "get_recent_messages",
    "clear_recent_messages",
    "increment_with_ttl",
    "check_rate_limit",
    "track_tokens_user",
    "track_tokens_chat",
    "get_user_daily_tokens",
    "acquire_chat_lock",
    "release_chat_lock",
    "enqueue_summarization_job",
    "dequeue_summarization_job",
]
