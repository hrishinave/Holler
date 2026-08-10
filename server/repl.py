"""v1 entrypoint: a plain REPL over the agent loop.

Run from the server/ dir:  uv --directory server run python repl.py

History persists in SQLite (see memory/store.py), so restarting the REPL resumes
the same conversation. Type '/reset' to forget it. Destructive-action
authorization is read from each message you type, exactly as it will be from a
Telegram message later.
"""

from __future__ import annotations

import asyncio

from agent.loop import run_turn
from config import settings
from context import set_conversation
from gate import is_authorized
from memory import store
from memory.reflect import maybe_reflect

_QUIT = {"quit", "exit", ":q", "q"}
_CONVERSATION_ID = "repl"


async def _ainput(prompt: str) -> str:
    """Non-blocking input so the event loop stays responsive."""
    return await asyncio.get_event_loop().run_in_executor(None, input, prompt)


async def main() -> None:
    print(f"personal-agent REPL  ·  model={settings.MODEL}  ·  tz={settings.HOME_TIMEZONE}")
    history = store.load_history(_CONVERSATION_ID)
    if history:
        print(f"(resumed conversation — {len(history)} messages)")
    print("Type a message. '/reset' to forget history, 'quit' to exit.\n")

    while True:
        try:
            user_text = (await _ainput("you › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in _QUIT:
            break
        if user_text.lower() == "/reset":
            store.clear(_CONVERSATION_ID)
            history = []
            print("history cleared\n")
            continue

        prior_len = len(history)
        set_conversation(_CONVERSATION_ID)
        result = await run_turn(
            user_text,
            history,
            authorized_destructive=is_authorized(user_text),
        )
        history = result.history
        # Persist just this turn's new messages (user + assistant/tool exchanges).
        store.append_messages(_CONVERSATION_ID, history[prior_len:])

        learned = await maybe_reflect(_CONVERSATION_ID)
        if learned:
            print(f"      · learned: {', '.join(learned)}")

        print(f"\nasst › {result.reply or '(no reply)'}")
        if result.tools_used:
            print(f"      · used: {', '.join(result.tools_used)}")
        if result.stop_reason.value != "completed":
            print(f"      · stop: {result.stop_reason.value}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
