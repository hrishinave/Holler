"""The BYOK seam: one provider-agnostic ``chat()``.

We drive any OpenAI-compatible endpoint with the official ``openai`` async client
pointed at ``LLM_BASE_URL`` with ``LLM_API_KEY``. Default is Google Gemini's
OpenAI-compat endpoint; OpenRouter, a local server, etc. are just a different
base URL + key + model name. Return shape is standard OpenAI JSON
(``choices[0].message`` with optional ``tool_calls``), so the loop and schemas
never change.

Caveat (documented, not fixable here): the OpenAI-compat layer normalizes the
*API*, not model *quality*. Tool-calling is reliable on Gemini/Claude, flaky on
small models.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from config import settings

_client: AsyncOpenAI | None = None


def _client_or_init() -> AsyncOpenAI:
    """Lazy singleton so importing this module never requires a key."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
    return _client


async def chat(messages: list, *, tools: list | None = None, system: str | None = None) -> dict:
    """One model turn. Returns the raw completion as a plain dict.

    ``messages`` is the running transcript (user/assistant/tool roles).
    ``tools`` is the JSON-schema tool list (or None for a plain reply).
    ``system`` is prepended as a system message when provided.
    """
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    kwargs: dict = {
        "model": settings.MODEL,
        "messages": msgs,
        "max_tokens": settings.MAX_TOKENS,
    }
    if tools:
        kwargs["tools"] = tools
    resp = await _client_or_init().chat.completions.create(**kwargs)
    return resp.model_dump()
