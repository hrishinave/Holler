"""Verification for the pending-action approval gate (variant B: execute the
stored args). Deterministic — the model is faked, and the destructive tool is a
local fake so nothing leaves the machine.

Covers the failures the old regex gate had, plus the new guarantees:
  - affirmative approval vs. negation veto ("please do not send it")
  - propose -> approve executes the STORED args verbatim
  - single-use (no replay), supersede (one "yes" = one action), TTL expiry
  - unattended (proactive) runs can never fire a destructive tool

Run:  uv --directory server run python scripts/verify_gate.py
"""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.loop as loop  # noqa: E402
import gate  # noqa: E402
from agent.tools.registry import TOOLS  # noqa: E402
from memory import facts  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from schemas import ToolResult, ToolSpec  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _tc(cid, name, args_json):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args_json}}


def _msg(content=None, tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def scripted_chat(responses):
    queue = list(responses)

    async def _fake(messages, *, tools=None, system=None):
        return queue.pop(0)

    return _fake


# A local fake destructive tool that records the exact args it ran with.
_calls: list[dict] = []


class SendArgs(BaseModel):
    to: str
    body: str


def _fake_send(to: str, body: str):
    _calls.append({"to": to, "body": body})
    return ToolResult.ok(note=f"sent to {to}")


async def main():
    gate.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    facts.set_connection(sqlite3.connect(":memory:", check_same_thread=False))  # keep system_prompt off the real DB
    TOOLS["fake_send"] = ToolSpec.from_model(
        name="fake_send", description="send a thing",
        args_model=SendArgs, handler=_fake_send, destructive=True,
    )

    try:
        print("1) reads_as_approval: affirm vs. negation veto")
        check("'yes, send it' approves", gate.reads_as_approval("yes, send it"))
        check("'go ahead' approves", gate.reads_as_approval("go ahead"))
        check("'please do not send it' VETOED (the old false-positive)",
              not gate.reads_as_approval("please do not send it"))
        check("'no, cancel that' vetoed", not gate.reads_as_approval("no, cancel that"))
        check("'what did I do yesterday' is not approval", not gate.reads_as_approval("what did I do yesterday"))
        check("'not now' is not approval", not gate.reads_as_approval("not now"))

        print("2) propose -> approve executes the STORED args")
        _calls.clear()
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tc("a1", "fake_send", '{"to":"sam@x.com","body":"hi"}')]),
            _msg(content="Send to sam@x.com: 'hi'. Confirm?"),
        ])
        turn = await loop.run_turn("email sam hi", [], conversation_id="c1")
        check("handler NOT run on proposal", _calls == [])
        check("result was needs_confirmation",
              any("needs_confirmation" in m.get("content", "") for m in turn.history if m.get("role") == "tool"))
        check("one pending recorded", gate.latest_pending("c1") is not None)

        loop.chat = scripted_chat([_msg(content="Sent.")])
        turn = await loop.run_turn("yes send it", [], conversation_id="c1")
        check("handler ran once on approval", len(_calls) == 1)
        check("stored args ran verbatim", _calls[-1] == {"to": "sam@x.com", "body": "hi"})
        check("pending consumed", gate.latest_pending("c1") is None)

        print("3) single-use: a second 'yes' does not replay")
        loop.chat = scripted_chat([_msg(content="Nothing pending.")])
        await loop.run_turn("yes", [], conversation_id="c1")
        check("no replay", len(_calls) == 1)

        print("4) negation veto blocks execution but keeps the proposal")
        _calls.clear()
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tc("b1", "fake_send", '{"to":"boss@x.com","body":"raise pls"}')]),
            _msg(content="Send to boss@x.com? Confirm?"),
        ])
        await loop.run_turn("email boss", [], conversation_id="c2")
        loop.chat = scripted_chat([_msg(content="Okay, holding off.")])
        await loop.run_turn("no wait, do not send it", [], conversation_id="c2")
        check("handler NOT run on refusal", _calls == [])
        check("proposal still pending after refusal", gate.latest_pending("c2") is not None)

        print("5) supersede: one 'yes' approves only the latest proposal")
        _calls.clear()
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tc("s1", "fake_send", '{"to":"first@x.com","body":"one"}')]),
            _msg(content="Send to first? Confirm?"),
        ])
        await loop.run_turn("email first", [], conversation_id="c3")
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tc("s2", "fake_send", '{"to":"second@x.com","body":"two"}')]),
            _msg(content="Send to second? Confirm?"),
        ])
        await loop.run_turn("actually email second", [], conversation_id="c3")
        loop.chat = scripted_chat([_msg(content="Sent.")])
        await loop.run_turn("yes send it", [], conversation_id="c3")
        check("only the latest action ran", len(_calls) == 1 and _calls[-1]["to"] == "second@x.com")

        print("6) TTL expiry: a stale proposal can't be approved")
        _calls.clear()
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tc("e1", "fake_send", '{"to":"late@x.com","body":"hi"}')]),
            _msg(content="Confirm?"),
        ])
        await loop.run_turn("email late", [], conversation_id="c4")
        # Age the pending past its TTL.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        conn = gate._connect()
        with conn:
            conn.execute("UPDATE pending_actions SET expires_at=? WHERE conversation_id='c4' AND status='pending'", (past,))
        loop.chat = scripted_chat([_msg(content="That expired.")])
        await loop.run_turn("yes send it", [], conversation_id="c4")
        check("expired proposal did not run", _calls == [])
        check("expired proposal cleared", gate.latest_pending("c4") is None)

        print("7) unattended (proactive) runs never fire a destructive tool")
        _calls.clear()
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tc("p1", "fake_send", '{"to":"anyone@x.com","body":"auto"}')]),
            _msg(content="I can't send without you."),
        ])
        turn = await loop.run_turn("send the summary", [], conversation_id="c5", interactive=False)
        check("handler NOT run unattended", _calls == [])
        check("nothing proposed unattended (no one to approve)", gate.latest_pending("c5") is None)
        # And an approval-looking prompt in an unattended run still can't fire it.
        loop.chat = scripted_chat([_msg(content="Still no.")])
        await loop.run_turn("yes send it", [], conversation_id="c5", interactive=False)
        check("unattended approval-looking text is inert", _calls == [])
    finally:
        TOOLS.pop("fake_send", None)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
