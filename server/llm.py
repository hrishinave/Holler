"""The BYOK seam: one provider-agnostic ``chat()``, via OpenRouter.

OpenRouter exposes an OpenAI-compatible endpoint, so we drive it with the
official ``openai`` async client pointed at OpenRouter's base URL. The whole app
makes exactly one kind of model call; swap models by changing ``MODEL`` in the
env. Return shape is standard OpenAI JSON (``choices[0].message`` with optional
``tool_calls``), so the loop and schemas never change.

Caveat (documented, not fixable here): OpenRouter normalizes the *API*, not model
*quality*. Tool-calling is reliable on Gemini/Claude, flaky on small models.
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
            base_url=settings.OPENROUTER_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            # Optional OpenRouter attribution; harmless if unused.
            default_headers={"X-Title": "personal-agent"},
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
