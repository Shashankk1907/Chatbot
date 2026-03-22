# utils/gemini_api.py
#
# Async LLM client — supports Ollama (local) and Gemini (cloud).
# Switchable via LLM_MODE env var.

import os
import asyncio
import httpx
from utils.config import (
    GEMINI_API_KEY, 
    LLM_MODE, 
    OLLAMA_BASE_URL, 
    OLLAMA_MODEL
)


# ── Async LLM call (used by ChatOrchestrator) ─────────────────────────────

async def call_llm_async(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4000,
) -> dict:
    """
    Async LLM call. Accepts a messages list (OpenAI-style format):
      [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

    Returns: {"role": "assistant", "content": "..."}
    Raises: RuntimeError on failure (caught by orchestrator for retry logic).
    """
    if LLM_MODE == "local":
        return await _call_ollama_async(messages, temperature)
    else:
        return await _call_gemini_async(messages, temperature, max_tokens)


async def _call_ollama_async(messages: list[dict], temperature: float) -> dict:
    """Call local Ollama model with messages formatted as a prompt."""
    # Ollama /api/generate expects a single prompt string
    prompt = _messages_to_prompt(messages)

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature},
                },
            )
            response.raise_for_status()
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
                "Run 'ollama serve' in a terminal."
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama HTTP {e.response.status_code}: {e}")

    data = response.json()
    text = data.get("response", "").strip()
    return {"role": "assistant", "content": text}


async def _call_gemini_async(
    messages: list[dict], temperature: float, max_tokens: int
) -> dict:
    """Call Google Gemini API (async via asyncio.to_thread)."""
    from google import genai

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    # google-genai SDK is sync-only, run in thread pool
    def _sync_call():
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Convert internal messages to Gemini contents format
        contents = _messages_to_gemini_contents(messages)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config={"temperature": temperature},
        )
        try:
            return response.text
        except Exception:
            return str(response)

    try:
        text = await asyncio.to_thread(_sync_call)
    except Exception as e:
        raise RuntimeError(f"Gemini API call failed: {e}")

    return {"role": "assistant", "content": text}


def _messages_to_gemini_contents(messages: list[dict]) -> list:
    """Convert internal messages list to Gemini native contents format."""
    from google.genai import types
    
    contents = []
    
    # Extract system prompt if present (Gemini handles it differently or we prepend it)
    # The SDK usually takes system_instruction in config, but we'll stick to contents for now
    # or handle 'system' role by prepending to the first user message if necessary.
    # Actually gemini-2.0-flash+ supports system_instruction.
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # Map OpenAI roles to Gemini roles ('user', 'model')
        gemini_role = "user" if role in ["user", "system"] else "model"
        
        parts = []
        if isinstance(content, str):
            parts.append(types.Part.from_text(text=content))
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, str):
                    parts.append(types.Part.from_text(text=p))
                elif isinstance(p, dict):
                    if "text" in p:
                        parts.append(types.Part.from_text(text=p["text"]))
                    elif "inline_data" in p:
                        parts.append(types.Part.from_bytes(
                            data=p["inline_data"]["data"],
                            mime_type=p["inline_data"]["mime_type"]
                        ))
        
        contents.append(types.Content(role=gemini_role, parts=parts))
        
    return contents


def _messages_to_prompt(messages: list[dict]) -> str:
    """Fallback for Ollama: Convert messages list to a single prompt string."""
    """Convert OpenAI-style messages list to a single prompt string."""
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


# ── Sync wrapper (backward compatibility) ─────────────────────────────────

def call_gemini(prompt_text: str, temperature: float = 0.7) -> dict:
    """Sync LLM call — wraps async call for backward compatibility."""
    messages = [{"role": "user", "content": prompt_text}]
    return asyncio.run(call_llm_async(messages, temperature))