"""Verification for memory compaction: rolling summary, clean turn-boundary cuts,
transcript validity, caching (no per-turn model call), and reset handling.
Deterministic — the summarizer model is faked.

Run:  uv --directory server run python scripts/verify_compaction.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory.summarize as summarize  # noqa: E402
from memory import store as mstore  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _turn(i):
    """One realistic turn: user, assistant(tool_calls), tool, assistant(reply)."""
    return [
        {"role": "user", "content": f"question {i}"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": f"c{i}", "type": "function",
                         "function": {"name": "calendar_list", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"},
        {"role": "assistant", "content": f"answer {i}"},
    ]


def _valid_transcript(msgs) -> bool:
    """Every 'tool' message must follow an assistant message with tool_calls."""
    seen_tool_call_ids = set()
    for m in msgs:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                seen_tool_call_ids.add(tc["id"])
        if m.get("role") == "tool":
            if m.get("tool_call_id") not in seen_tool_call_ids:
                return False
    return True


calls = {"n": 0}


def fake_chat(summary_text):
    async def _fake(messages, *, tools=None, system=None):
        calls["n"] += 1
        return {"choices": [{"message": {"role": "assistant", "content": summary_text}}]}
    return _fake


async def main():
    mstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    summarize.chat = fake_chat("SUMMARY-A")
    conv = "tg:1"

    print("1) below threshold: untouched, no model call")
    small = _turn(1) + _turn(2)  # 8 messages < COMPACT_THRESHOLD (30)
    out = await summarize.compact(conv, small)
    check("returns list unchanged", out == small)
    check("no summarization call", calls["n"] == 0)

    print("2) above threshold: compacts to summary note + recent tail")
    long = []
    for i in range(12):  # 48 messages
        long += _turn(i)
    out = await summarize.compact(conv, long)
    check("first message is a system summary note", out[0]["role"] == "system" and "SUMMARY-A" in out[0]["content"])
    check("view is much shorter than raw", len(out) < len(long))
    check("summarization ran once", calls["n"] == 1)

    print("3) clean cut: tail starts on a user message (no split tool exchange)")
    check("recent tail begins with a user turn", out[1]["role"] == "user")
    check("view is a valid transcript", _valid_transcript(out))
    check("checkpoint persisted", mstore.get_summary(conv) is not None)

    print("4) caching: next turn reuses summary, NO new model call")
    long2 = long + _turn(99)  # a couple more messages, tail still small
    before = calls["n"]
    out2 = await summarize.compact(conv, long2)
    check("no new summarization call", calls["n"] == before)
    check("still summary + tail", out2[0]["role"] == "system" and out2[1]["role"] == "user")

    print("5) rolling: once tail grows past margin, re-summarize + advance checkpoint")
    summarize.chat = fake_chat("SUMMARY-B")
    covered_before = mstore.get_summary(conv)[0]
    big = long + [msg for i in range(20, 30) for msg in _turn(i)]  # +40 messages
    before = calls["n"]
    out3 = await summarize.compact(conv, big)
    check("re-summarized once", calls["n"] == before + 1)
    check("checkpoint advanced", mstore.get_summary(conv)[0] > covered_before)
    check("new summary in note", "SUMMARY-B" in out3[0]["content"])
    check("rolled view still valid", _valid_transcript(out3))

    print("6) reset: stale checkpoint (covered > n) is dropped")
    # Simulate /reset: history now tiny but checkpoint covers many.
    tiny = _turn(1)
    out4 = await summarize.compact(conv, tiny)
    check("stale checkpoint cleared", mstore.get_summary(conv) is None)
    check("returns tiny history unchanged", out4 == tiny)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
