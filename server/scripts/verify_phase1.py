"""Phase 1 verification: exercise the loop, registry, and gate deterministically
by faking the model, so no OpenRouter key is needed.

Run from repo root:  uv --directory server run python scripts/verify_phase1.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.loop as loop  # noqa: E402
from agent.tools.registry import TOOLS, TOOL_SCHEMAS, execute_tool  # noqa: E402
from gate import is_authorized  # noqa: E402
from schemas import ToolResult, ToolSpec  # noqa: E402
from pydantic import BaseModel  # noqa: E402


def _tool_call(cid, name, args_json):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args_json}}


def _msg(content=None, tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def scripted_chat(responses):
    """Return a fake async chat() that yields queued responses in order."""
    queue = list(responses)

    async def _fake(messages, *, tools=None, system=None):
        return queue.pop(0)

    return _fake


ok = 0
fail = 0


def check(label, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        fail += 1
        print(f"  ✗ {label}")


async def main():
    print("1) registry + schema")
    check("clock tool registered", "get_current_time" in TOOLS)
    schema = TOOL_SCHEMAS[0]["function"]
    check("advertised schema has name", schema["name"] == "get_current_time")
    check("schema advertises 'timezone' param", "timezone" in schema["parameters"]["properties"])

    print("2) execute_tool directly")
    res = await execute_tool("get_current_time", {"timezone": "Asia/Tokyo"})
    check("clock returns ok", res["status"] == "ok")
    check("clock reports Tokyo tz", res["data"]["timezone"] == "Asia/Tokyo")
    bad = await execute_tool("get_current_time", {"timezone": "Not/AZone"})
    check("bad timezone -> error", bad["status"] == "error")
    unknown = await execute_tool("nope", {})
    check("unknown tool -> error", unknown["status"] == "error")

    print("3) gate.is_authorized word-boundary logic")
    check("'yes, send it' authorized", is_authorized("yes, send it"))
    check("'go ahead' authorized", is_authorized("go ahead"))
    check("'what did I do yesterday' NOT authorized", not is_authorized("what did I do yesterday"))
    check("'not now' NOT authorized", not is_authorized("not now"))

    print("4) loop: tool round-trip then reply")
    loop.chat = scripted_chat([
        _msg(tool_calls=[_tool_call("c1", "get_current_time", '{"timezone": "Asia/Tokyo"}')]),
        _msg(content="It's the middle of the night in Tokyo."),
    ])
    turn = await loop.run_turn("what time is it in tokyo?", [])
    check("used the clock tool", turn.tools_used == ["get_current_time"])
    check("returned the final text reply", "Tokyo" in turn.reply)
    check("stop_reason completed", turn.stop_reason.value == "completed")
    check("transcript has a tool message", any(m.get("role") == "tool" for m in turn.history))

    print("5) loop: destructive gate (inject a fake destructive tool)")

    called = {"n": 0}

    class FakeArgs(BaseModel):
        target: str

    def _fake_delete(target: str):
        called["n"] += 1
        return ToolResult.ok(note=f"deleted {target}")

    TOOLS["fake_delete"] = ToolSpec.from_model(
        name="fake_delete", description="delete a thing",
        args_model=FakeArgs, handler=_fake_delete, destructive=True,
    )
    try:
        # Unauthorized: loop must refuse and NOT call the handler.
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tool_call("d1", "fake_delete", '{"target": "x"}')]),
            _msg(content="Want me to delete x? Confirm and I will."),
        ])
        turn = await loop.run_turn("delete x", [])
        check("handler NOT called when unauthorized", called["n"] == 0)
        tool_msgs = [m for m in turn.history if m.get("role") == "tool"]
        check("blocked result is needs_confirmation", "needs_confirmation" in tool_msgs[0]["content"])

        # Authorized: loop runs the handler.
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tool_call("d2", "fake_delete", '{"target": "x"}')]),
            _msg(content="Done, deleted x."),
        ])
        turn = await loop.run_turn("yes, delete x", [], authorized_destructive=True)
        check("handler called when authorized", called["n"] == 1)
        check("final reply after delete", "deleted x" in turn.reply.lower())
    finally:
        TOOLS.pop("fake_delete", None)

    print("6) loop: CONDITIONAL gate (calendar_create-style: gated only w/ attendees)")

    created = {"n": 0}

    class CreateArgs(BaseModel):
        title: str
        attendees: list[str] = []

    def _fake_create(title: str, attendees: list[str] | None = None):
        created["n"] += 1
        return ToolResult.ok(note=f"created {title}")

    TOOLS["fake_create"] = ToolSpec.from_model(
        name="fake_create", description="create an event",
        args_model=CreateArgs, handler=_fake_create,
        destructive_when=lambda a: bool(a.get("attendees")),
    )
    try:
        # Solo event (no attendees), unauthorized -> should still run.
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tool_call("e1", "fake_create", '{"title": "gym"}')]),
            _msg(content="Blocked 3-4 for gym."),
        ])
        await loop.run_turn("block 3-4 for gym", [])
        check("solo create runs without confirmation", created["n"] == 1)

        # Event with attendees, unauthorized -> must be gated (not created).
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tool_call("e2", "fake_create",
                                        '{"title": "sync", "attendees": ["a@b.com"]}')]),
            _msg(content="That invites a@b.com. Confirm and I'll send it."),
        ])
        turn = await loop.run_turn("set up a sync with a@b.com", [])
        check("create w/ attendees NOT run when unauthorized", created["n"] == 1)
        tmsgs = [m for m in turn.history if m.get("role") == "tool"]
        check("attendee create blocked as needs_confirmation",
              "needs_confirmation" in tmsgs[0]["content"])

        # Event with attendees, authorized -> runs.
        loop.chat = scripted_chat([
            _msg(tool_calls=[_tool_call("e3", "fake_create",
                                        '{"title": "sync", "attendees": ["a@b.com"]}')]),
            _msg(content="Sent the invite."),
        ])
        await loop.run_turn("yes set up the sync with a@b.com", [], authorized_destructive=True)
        check("attendee create runs when authorized", created["n"] == 2)
    finally:
        TOOLS.pop("fake_create", None)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
