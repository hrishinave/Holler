"""The BYOK seam: one provider-agnostic ``chat()``.

We drive any OpenAI-compatible endpoint with the official ``openai`` async client
pointed at ``LLM_BASE_URL`` with ``LLM_API_KEY``. Default is Google Gemini's
OpenAI-compat endpoint; OpenRouter, a local server, etc. are just a different
base URL + key + model name. Return shape is standard OpenAI JSON
(``choices[0].message`` with optional ``tool_calls``), so the loop and schemas
never change.

Transient failures are retried with backoff: a per-minute rate limit (429) is
common on free tiers, and a multi-step turn makes several calls, so one 429 must
not kill the turn. We honor the server's suggested retry delay when it gives one.
Per-*day* quota exhaustion is NOT retried — waiting can't clear it inside a turn.

Caveat (documented, not fixable here): the OpenAI-compat layer normalizes the
*API*, not model *quality*. Tool-calling is reliable on Gemini/Claude, flaky on
small models.
"""

from __future__ import annotations

import asyncio
import re

from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from config import settings

_client: AsyncOpenAI | None = None

# Retry budget. Waits are capped so a mis-parsed or per-day delay can't hang a
# turn for minutes; a per-minute reset (~52s on Gemini free) fits in one wait.
_MAX_ATTEMPTS = 4
_MAX_SLEEP = 60.0


def _client_or_init() -> AsyncOpenAI:
    """Lazy singleton so importing this module never requires a key."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
    return _client


def _suggested_delay(err: Exception) -> float | None:
    """The server's requested retry delay in seconds, if the error carries one."""
    text = str(err)
    m = re.search(r"retry in ([\d.]+)s", text) or re.search(r"retryDelay['\"]?:?\s*['\"]?(\d+)s", text)
    return float(m.group(1)) if m else None


def _is_daily_quota(err: Exception) -> bool:
    """A per-day quota won't clear within a turn — don't burn retries on it."""
    text = str(err).lower()
    return "perday" in text or "per_day" in text or "per day" in text


async def chat(messages: list, *, tools: list | None = None, system: str | None = None) -> dict:
    """One model turn. Returns the raw completion as a plain dict.

    ``messages`` is the running transcript (user/assistant/tool roles).
    ``tools`` is the JSON-schema tool list (or None for a plain reply).
    ``system`` is prepended as a system message when provided.

    Retries transient errors (429 rate limits, timeouts, 5xx) with backoff.
    """
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    kwargs: dict = {
        "model": settings.MODEL,
        "messages": msgs,
        "max_tokens": settings.MAX_TOKENS,
    }
    if tools:
        kwargs["tools"] = tools

    client = _client_or_init()
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            resp = await client.chat.completions.create(**kwargs)
            return resp.model_dump()
        except RateLimitError as err:
            # Per-day exhaustion won't recover in-turn: fail fast so the channel
            # can tell the user, instead of stalling.
            if _is_daily_quota(err) or last:
                raise
            delay = min(_suggested_delay(err) or 2 ** attempt, _MAX_SLEEP)
            print(f"[llm] rate limited; retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{_MAX_ATTEMPTS})", flush=True)
            await asyncio.sleep(delay)
        except (APITimeoutError, APIConnectionError) as err:
            if last:
                raise
            delay = min(2 ** attempt, _MAX_SLEEP)
            print(f"[llm] {type(err).__name__}; retrying in {delay:.0f}s", flush=True)
            await asyncio.sleep(delay)
        except APIStatusError as err:
            # Retry server errors (5xx); surface client errors (4xx) immediately.
            if (err.status_code or 0) >= 500 and not last:
                await asyncio.sleep(min(2 ** attempt, _MAX_SLEEP))
            else:
                raise
    # Unreachable: the final attempt always returns or raises.
    raise RuntimeError("chat() exhausted retries without returning")
