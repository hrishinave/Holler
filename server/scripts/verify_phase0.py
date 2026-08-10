"""Phase 0 smoke test: config loads, llm imports, and (if a key is present)
chat() does a real OpenRouter round-trip.

Run from repo root:  uv run python scripts/verify_phase0.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402
from llm import chat  # noqa: E402


async def main() -> None:
    print(f"MODEL              = {settings.MODEL}")
    print(f"LLM_BASE_URL       = {settings.LLM_BASE_URL}")
    print(f"LLM_API_KEY set    = {bool(settings.LLM_API_KEY)}")
    print(f"HOME_TIMEZONE      = {settings.HOME_TIMEZONE}")
    print(f"COMPOSIO_ENTITY    = {settings.COMPOSIO_ENTITY_ID or '(unset)'}")
    print(f"COMPOSIO_KEY set   = {bool(settings.COMPOSIO_API_KEY)}")
    print("config + llm imports OK ✓")

    if not settings.LLM_API_KEY:
        print("\nNo LLM_API_KEY set — skipping live call.")
        print("Add it to .env and re-run.")
        return

    print("\nLLM_API_KEY found — doing a live round-trip...")
    resp = await chat(
        [{"role": "user", "content": "Reply with exactly the word: pong"}],
        system="You are a terse assistant.",
    )
    reply = resp["choices"][0]["message"]["content"]
    print(f"model replied: {reply!r}")
    print("live chat() round-trip OK ✓")


if __name__ == "__main__":
    asyncio.run(main())
