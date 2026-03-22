# utils/token_counter.py
#
# Token counting utility.
# Uses tiktoken (cl100k_base encoding) which closely approximates
# token counts for most modern LLMs including Phi-3 and Gemini.

import tiktoken

# Cache the encoding object — expensive to create, reuse across calls.
_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text) -> int:
    """Count tokens in a string or list of parts using cl100k_base encoding."""
    if isinstance(text, str):
        return len(_encoding.encode(text))
    if isinstance(text, list):
        total = 0
        for part in text:
            if isinstance(part, str):
                total += len(_encoding.encode(part))
            elif isinstance(part, dict) and "text" in part:
                total += len(_encoding.encode(part["text"]))
            # Binary parts (inline_data) are ignored for basic token counting
        return total
    return 0


def count_messages_tokens(messages: list[dict]) -> int:
    """
    Count total tokens across a list of message dicts.
    Each message must have at minimum a 'content' key.
    Adds 4 tokens per message for role/formatting overhead (OpenAI convention).
    """
    total = 0
    for msg in messages:
        total += 4  # role + formatting overhead
        content = msg.get("content", "")
        total += count_tokens(content)
    return total