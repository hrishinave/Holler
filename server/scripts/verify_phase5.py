"""Phase 5 verification: triggers store, trigger tools, and the scheduler —
deterministically (no Telegram, no model, no real clock dependence).

Run:  uv --directory server run python scripts/verify_phase5.py
"""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import context  # noqa: E402
from agent.tools.registry import TOOLS, DESTRUCTIVE_TOOLS, execute_tool  # noqa: E402
from memory import store as mstore  # noqa: E402
from proactivity import store as pstore  # noqa: E402
from proactivity.notifier import _chat_id  # noqa: E402
from proactivity.scheduler import Scheduler, _next_fire  # noqa: E402
from schemas import TurnResult  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


PAST = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
FUTURE = _iso(datetime.now(timezone.utc) + timedelta(days=1))


async def main():
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    mstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))

    print("1) store CRUD + due query")
    t = pstore.create("tg:123", "remind them", FUTURE)
    check("create returns id", t.id is not None)
    check("future trigger NOT due", pstore.due(pstore.utcnow_iso()) == [])
    pstore.reschedule(t.id, PAST)
    due = pstore.due(pstore.utcnow_iso())
    check("past trigger IS due", len(due) == 1 and due[0].id == t.id)
    check("list_for finds active", len(pstore.list_for("tg:123")) == 1)
    check("cancel works", pstore.cancel(t.id) is True)
    check("cancelled not due", pstore.due(pstore.utcnow_iso()) == [])
    check("cancel again is False", pstore.cancel(t.id) is False)

    print("2) helpers")
    check("_chat_id extracts from tg:", _chat_id("tg:999") == "999")
    check("_chat_id None for repl", _chat_id("repl") is None)
    nxt = _next_fire("daily", PAST)
    check("_next_fire advances to future", nxt is not None and nxt > pstore.utcnow_iso())
    check("_next_fire None for one-shot", _next_fire(None, PAST) is None)

    print("3) trigger tools via execute_tool (with conversation context)")
    context.set_conversation("tg:123")
    r = await execute_tool("trigger_create", {"when": FUTURE, "task": "call mom"})
    check("trigger_create ok", r["status"] == "ok")
    new_id = r["data"]["id"]
    lst = await execute_tool("trigger_list", {})
    check("trigger_list shows it", lst["data"]["count"] == 1 and lst["data"]["triggers"][0]["id"] == new_id)
    check("trigger tools not gated", not (DESTRUCTIVE_TOOLS & {"trigger_create", "trigger_list", "trigger_cancel"}))
    bad = await execute_tool("trigger_create", {"when": "not-a-time", "task": "x"})
    check("bad time -> error", bad["status"] == "error")
    canc = await execute_tool("trigger_cancel", {"trigger_id": new_id})
    check("trigger_cancel ok", canc["data"]["cancelled"] is True)

    print("4) no-context guard")
    context.set_conversation("")
    nc = await execute_tool("trigger_create", {"when": FUTURE, "task": "x"})
    check("no conversation -> error", nc["status"] == "error")

    print("5) scheduler fires a due one-shot trigger")
    sent = []
    captured = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    async def fake_runner(prompt, history, *, conversation_id="", interactive=True):
        captured.append({"prompt": prompt, "interactive": interactive})
        return TurnResult(reply=f"[proactive] {prompt}",
                          history=list(history) + [{"role": "assistant", "content": "x"}], iterations=1)

    sched = Scheduler(send=fake_send, runner=fake_runner)
    one = pstore.create("tg:555", "remind them to stretch", PAST)
    await sched.tick()
    check("runner ran the trigger prompt", captured and captured[-1]["prompt"] == "remind them to stretch")
    check("proactive turn is non-interactive (gate-closed)", captured[-1]["interactive"] is False)
    check("reply sent to derived chat id", sent and sent[-1][0] == "555" and "stretch" in sent[-1][1])
    check("one-shot marked done", pstore.get(one.id).status.value == "done")
    check("proactive reply persisted to memory", mstore.count("tg:555") >= 1)

    print("6) scheduler reschedules a recurring trigger")
    rec = pstore.create("tg:555", "daily summary", PAST, repeat="daily")
    await sched.tick()
    after = pstore.get(rec.id)
    check("recurring stays active", after.status.value == "active")
    check("recurring advanced to future", after.next_trigger > pstore.utcnow_iso())

    print("7) future trigger is left alone")
    fut = pstore.create("tg:555", "later", FUTURE)
    before = len(captured)
    await sched.tick()
    check("future trigger not fired", len(captured) == before and pstore.get(fut.id).status.value == "active")

    context.set_conversation("")
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
