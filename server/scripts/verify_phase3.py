"""Phase 3 verification: the conversation log persists and reconstructs a valid
transcript. Uses an in-memory SQLite connection (no file, no cleanup).

Run:  uv --directory server run python scripts/verify_phase3.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import store  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


# A representative one-turn transcript: user -> assistant(tool_calls) -> tool -> assistant(reply)
TURN = [
    {"role": "user", "content": "what's on my calendar?"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "calendar_list", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok", "data": {"count": 0}}'},
    {"role": "assistant", "content": "Nothing on the calendar."},
]


def main():
    store.set_connection(sqlite3.connect(":memory:"))
    conv = "test-conv"

    print("1) empty to start")
    check("no history initially", store.load_history(conv) == [])
    check("count 0", store.count(conv) == 0)

    print("2) append a turn's delta + reload")
    store.append_messages(conv, TURN)
    loaded = store.load_history(conv)
    check("all 4 messages persisted", len(loaded) == 4)
    check("order preserved", [m["role"] for m in loaded] == ["user", "assistant", "tool", "assistant"])
    check("tool_calls survive round-trip", loaded[1]["tool_calls"][0]["function"]["name"] == "calendar_list")
    check("tool result content survives", '"count": 0' in loaded[2]["content"])
    check("final reply intact", loaded[3]["content"] == "Nothing on the calendar.")

    print("3) simulate resume: next turn sees prior history")
    prior = store.load_history(conv)
    next_delta = [{"role": "user", "content": "and tomorrow?"},
                  {"role": "assistant", "content": "Also clear."}]
    store.append_messages(conv, next_delta)
    full = store.load_history(conv)
    check("history grows by delta", len(full) == 6)
    check("prior turn still first", full[0]["content"] == "what's on my calendar?")

    print("4) isolation + clear")
    store.append_messages("other-conv", [{"role": "user", "content": "hi"}])
    check("conversations isolated", store.count(conv) == 6 and store.count("other-conv") == 1)
    store.clear(conv)
    check("clear empties one conversation", store.load_history(conv) == [])
    check("other conversation untouched", store.count("other-conv") == 1)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
