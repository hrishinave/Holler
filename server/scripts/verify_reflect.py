"""Verification for autonomous reflection: it extracts durable facts from a
conversation, debounces, dedups, and advances its marker. Deterministic — the
model is faked.

Run:  uv --directory server run python scripts/verify_reflect.py
"""

import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory.reflect as reflect  # noqa: E402
from memory import facts  # noqa: E402
from memory import store as mstore  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _seed(conv, pairs):
    """Append (role, content) message pairs to the store."""
    msgs = [{"role": r, "content": c} for r, c in pairs]
    mstore.append_messages(conv, msgs)


def fake_chat_returning(text, captured):
    async def _fake(messages, *, tools=None, system=None):
        captured.append({"system": system, "user": messages[-1]["content"]})
        return {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return _fake


async def main():
    mstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    facts.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    conv = "tg:1"

    print("1) debounce: below threshold, nothing happens")
    _seed(conv, [("user", "hey"), ("assistant", "hi")])  # 2 msgs < REFLECT_AFTER
    captured = []
    reflect.chat = fake_chat_returning("Dislikes mornings", captured)
    learned = await reflect.maybe_reflect(conv)
    check("no reflection below threshold", learned == [] and captured == [])

    print("2) reflection extracts new facts once enough messages accrue")
    _seed(conv, [("user", "ugh another 8am standup, i hate mornings"),
                 ("assistant", "brutal"),
                 ("user", "my manager priya scheduled it"),
                 ("assistant", "noted"),
                 ("user", "anyway what's next"),
                 ("assistant", "nothing today")])  # now >= 8 total
    reflect.chat = fake_chat_returning("Dislikes early/morning meetings\nManager is Priya", captured)
    learned = await reflect.maybe_reflect(conv)
    check("learned two facts", len(learned) == 2)
    stored = [f["content"] for f in facts.list_facts()]
    check("facts persisted", "Manager is Priya" in stored and "Dislikes early/morning meetings" in stored)
    check("existing facts shown to the model (dedup context)", "already know" in captured[-1]["user"].lower())

    print("3) marker advances: a second pass with no new messages does nothing")
    captured2 = []
    reflect.chat = fake_chat_returning("Dislikes mornings", captured2)
    learned = await reflect.reflect(conv)
    check("no new messages -> no model call, nothing learned", learned == [] and captured2 == [])

    print("4) NONE reply yields no facts")
    _seed(conv, [("user", "the weather is nice"), ("assistant", "it is"),
                 ("user", "ok"), ("assistant", "yep"),
                 ("user", "cool"), ("assistant", "indeed"),
                 ("user", "bye"), ("assistant", "later")])
    before = len(facts.list_facts())
    reflect.chat = fake_chat_returning("NONE", [])
    learned = await reflect.maybe_reflect(conv)
    check("NONE -> nothing learned", learned == [])
    check("fact count unchanged", len(facts.list_facts()) == before)

    print("5) dedup: a re-extracted known fact isn't stored twice")
    _seed(conv, [("user", "reminder my manager is priya"), ("assistant", "yep"),
                 ("user", "a"), ("assistant", "b"),
                 ("user", "c"), ("assistant", "d"),
                 ("user", "e"), ("assistant", "f")])
    reflect.chat = fake_chat_returning("Manager is Priya", [])  # already known
    learned = await reflect.maybe_reflect(conv)
    check("duplicate fact not re-stored", learned == [])
    check("still only one 'Manager is Priya'",
          sum(1 for f in facts.list_facts() if f["content"] == "Manager is Priya") == 1)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
