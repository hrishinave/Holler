"""Verification for structured reflection: it turns conversation into typed memory
proposals, distinguishes told/inferred, supersedes corrections, refuses sensitive
data, debounces on user turns, and advances its marker. Deterministic — the model
is faked to emit JSON proposals.

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
    mstore.append_messages(conv, [{"role": r, "content": c} for r, c in pairs])


def fake_emitting(memories, captured=None):
    """A fake chat() that returns the given proposals as JSON."""
    async def _fake(messages, *, tools=None, system=None):
        if captured is not None:
            captured.append({"system": system})
        return {"choices": [{"message": {"content": json.dumps({"memories": memories})}}]}
    return _fake


async def main():
    mstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    facts.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    conv = "tg:1"

    print("1) debounce: below the user-turn threshold, nothing happens")
    _seed(conv, [("user", "hey"), ("assistant", "hi")])  # 1 user turn < 3
    reflect.chat = fake_emitting([{"operation": "add", "kind": "preference",
        "content": "x", "source": "explicit", "strength": "preference"}])
    learned = await reflect.maybe_reflect(conv)
    check("no reflection below threshold", learned == [])

    print("2) structured extraction stores typed beliefs")
    _seed(conv, [("user", "my manager is priya"), ("assistant", "ok"),
                 ("user", "i like terse emails"), ("assistant", "noted"),
                 ("user", "anyway"), ("assistant", "sure")])  # now >=3 user turns
    captured = []
    reflect.chat = fake_emitting([
        {"operation": "add", "kind": "relationship", "canonical_key": "relationship.manager",
         "content": "Priya", "source": "explicit", "strength": "preference"},
        {"operation": "add", "kind": "preference", "canonical_key": "preference.email_style",
         "content": "Terse emails", "source": "explicit", "strength": "preference"},
    ], captured)
    learned = await reflect.maybe_reflect(conv)
    check("two beliefs learned", len(learned) == 2)
    check("uses the dedicated reflection prompt (not voice)",
          "canonical_key" in (captured[-1]["system"] or ""))
    stored = {m["canonical_key"]: m for m in facts.list_memories()}
    check("manager stored under its key", stored.get("relationship.manager", {}).get("content") == "Priya")

    print("3) a correction supersedes via the same key")
    _seed(conv, [("user", "actually dev is my manager now"), ("assistant", "got it"),
                 ("user", "yep"), ("assistant", "ok"), ("user", "cool"), ("assistant", "k")])
    reflect.chat = fake_emitting([
        {"operation": "supersede", "kind": "relationship", "canonical_key": "relationship.manager",
         "content": "Dev", "source": "corrected", "strength": "preference"}])
    await reflect.maybe_reflect(conv)
    mgr = [m for m in facts.list_memories() if m["canonical_key"] == "relationship.manager"]
    check("one active manager after correction", len(mgr) == 1 and mgr[0]["content"] == "Dev")

    print("4) an inferred hypothesis cannot override a stated fact")
    facts.add_memory("Chicago", kind="identity", source="explicit", strength="preference",
                     canonical_key="identity.city")
    _seed(conv, [("user", "the weather here is grey"), ("assistant", "mm"),
                 ("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")])
    reflect.chat = fake_emitting([
        {"operation": "add", "kind": "identity", "canonical_key": "identity.city",
         "content": "London", "source": "inferred", "strength": "hypothesis"}])
    await reflect.maybe_reflect(conv)
    city = [m for m in facts.list_memories() if m["canonical_key"] == "identity.city"]
    check("stated city stands over inference", len(city) == 1 and city[0]["content"] == "Chicago")

    print("5) sensitive proposals are refused by the boundary")
    _seed(conv, [("user", "ugh my therapy appt got moved"), ("assistant", "mm"),
                 ("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")])
    before = len(facts.list_memories())
    reflect.chat = fake_emitting([
        {"operation": "add", "kind": "fact", "content": "User is in therapy for anxiety",
         "source": "inferred", "strength": "hypothesis"},
        {"operation": "add", "kind": "preference", "canonical_key": "preference.reminders",
         "content": "Prefers direct reminders", "source": "inferred", "strength": "hypothesis"}])
    learned = await reflect.maybe_reflect(conv)
    check("sensitive proposal not stored", not any("therapy" in m["content"].lower() or "anxiety" in m["content"].lower()
                                                   for m in facts.list_memories()))
    check("the benign proposal alongside it still stored",
          any(m["content"] == "Prefers direct reminders" for m in facts.list_memories()))

    print("6) empty proposals + marker advance")
    _seed(conv, [("user", "ok bye"), ("assistant", "later"),
                 ("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")])
    reflect.chat = fake_emitting([])
    check("no beliefs from empty proposals", await reflect.maybe_reflect(conv) == [])
    captured2 = []
    reflect.chat = fake_emitting([{"operation": "add", "kind": "fact", "content": "z",
                                   "source": "explicit", "strength": "preference"}], captured2)
    check("no new messages -> no model call", await reflect.reflect(conv) == [] and captured2 == [])

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
