"""Phase 2 verification: calendar + gmail tools against a FAKE Composio client
(no network, no real account). Proves normalization, the timezone-applied-once
rule, gate flags, and loop integration.

Run:  uv --directory server run python scripts/verify_phase2.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.loop as loop  # noqa: E402
import gate  # noqa: E402
from agent.tools import _composio, calendar as cal  # noqa: E402
from agent.tools.registry import TOOLS, TOOL_SCHEMAS, DESTRUCTIVE_TOOLS, execute_tool  # noqa: E402


# --- fake Composio client -----------------------------------------------------
class _Tools:
    def __init__(self, outer):
        self.outer = outer

    def execute(self, slug, user_id=None, arguments=None, **kw):
        self.outer.calls.append((slug, dict(arguments or {})))
        resp = self.outer.responses.get(slug)
        if callable(resp):
            resp = resp(arguments)
        return {"successful": True, "data": resp if resp is not None else {}}


class FakeComposio:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.tools = _Tools(self)


EV_TIMED = {
    "id": "ev1", "summary": "Lunch", "location": "Cafe", "status": "confirmed",
    "start": {"dateTime": "2026-08-10T12:00:00-05:00", "timeZone": "America/Chicago"},
    "end": {"dateTime": "2026-08-10T13:00:00-05:00"},
    "attendees": [{"email": "a@b.com", "responseStatus": "accepted"}],
    "organizer": {"email": "me@x.com"}, "htmlLink": "http://link",
}
EV_ALLDAY = {"id": "ev2", "summary": "Holiday", "start": {"date": "2026-12-25"}, "end": {"date": "2026-12-26"}}

MSG1 = {
    "messageId": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"],
    "messageText": "Hi there, can we meet?",
    "payload": {"headers": [
        {"name": "From", "value": "Alice <alice@example.com>"},
        {"name": "To", "value": "me@x.com"},
        {"name": "Subject", "value": "Meeting?"},
        {"name": "Date", "value": "Mon, 10 Aug 2026 09:00:00 -0500"},
    ]},
}

RESPONSES = {
    "GOOGLECALENDAR_EVENTS_LIST": {"timeZone": "America/Chicago", "items": [EV_TIMED, EV_ALLDAY]},
    "GOOGLECALENDAR_CREATE_EVENT": {"response_data": {**EV_TIMED, "id": "new1", "summary": "Sync"}},
    "GOOGLECALENDAR_DELETE_EVENT": {},
    "GMAIL_FETCH_EMAILS": {"messages": [MSG1]},
    "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID": MSG1,
    "GMAIL_CREATE_EMAIL_DRAFT": {"id": "draft123", "threadId": "t1"},
    "GMAIL_SEND_EMAIL": {"id": "m999", "threadId": "t1", "labelIds": ["SENT"]},
}

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _tool_call(cid, name, args_json):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args_json}}


def _msg(content=None, tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def scripted_chat(responses):
    q = list(responses)

    async def _fake(messages, *, tools=None, system=None):
        return q.pop(0)

    return _fake


async def main():
    fake = FakeComposio(RESPONSES)
    _composio.set_client(fake)
    gate.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    cal._cached_tz = None  # reset tz cache

    print("1) registry integration")
    for name in ("calendar_list", "calendar_create", "calendar_delete",
                 "gmail_search", "gmail_get", "gmail_send", "gmail_reply"):
        check(f"{name} registered", name in TOOLS)
    check("no Gmail draft tool (compose is in-chat only)", "gmail_draft" not in TOOLS)
    check("schemas advertised for all", len(TOOL_SCHEMAS) == len(TOOLS))
    check("DESTRUCTIVE = delete/send/reply",
          DESTRUCTIVE_TOOLS == {"calendar_delete", "gmail_send", "gmail_reply"})
    check("create gated only w/ attendees (has)", TOOLS["calendar_create"].is_destructive({"attendees": ["a@b.com"]}))
    check("create NOT gated w/o attendees", not TOOLS["calendar_create"].is_destructive({}))

    print("2) calendar_list normalization + tz capture")
    res = await execute_tool("calendar_list", {"max_results": 5})
    check("returns ok", res["status"] == "ok")
    evs = res["data"]["events"]
    check("2 events", res["data"]["count"] == 2)
    check("timed event summary", evs[0]["summary"] == "Lunch")
    check("timed event not all-day", evs[0]["all_day"] is False)
    check("timed start has dateTime", evs[0]["start"]["date_time"] == "2026-08-10T12:00:00-05:00")
    check("attendee normalized", evs[0]["attendees"][0]["email"] == "a@b.com")
    check("all-day event detected", evs[1]["all_day"] is True)
    check("tz auto-detected from list", cal.resolve_timezone() == "America/Chicago")

    print("3) calendar_create: timezone applied exactly once")
    fake.calls.clear()
    # Offset-aware start, no explicit tz -> resolver gives America/Chicago (CDT, -05:00).
    await execute_tool("calendar_create", {"summary": "Sync", "start": "2026-08-11T14:00:00-05:00",
                                           "end": "2026-08-11T14:30:00-05:00"})
    create_args = [a for s, a in fake.calls if s == "GOOGLECALENDAR_CREATE_EVENT"][0]
    check("start_datetime is naive (no offset)", create_args["start_datetime"] == "2026-08-11T14:00:00")
    check("timezone set to calendar tz", create_args["timezone"] == "America/Chicago")
    check("duration derived (0h 30m)",
          create_args["event_duration_hour"] == 0 and create_args["event_duration_minutes"] == 30)

    print("4) gmail search/get normalization")
    r = await execute_tool("gmail_search", {"query": "is:unread"})
    m0 = r["data"]["messages"][0]
    check("sender parsed to address+name", m0["sender"]["email"] == "alice@example.com" and m0["sender"]["name"] == "Alice")
    check("subject from headers", m0["subject"] == "Meeting?")
    check("unread flag from labels", m0["unread"] is True)
    full = await execute_tool("gmail_get", {"message_id": "m1"})
    check("get includes body", "meet" in (full["data"]["body"] or "").lower())

    print("5) gmail_reply derives recipient/subject/thread from original")
    fake.calls.clear()
    rep = await execute_tool("gmail_reply", {"message_id": "m1", "body": "sure, when?"})
    send_args = [a for s, a in fake.calls if s == "GMAIL_SEND_EMAIL"][0]
    check("reply goes to original sender", send_args["recipient_email"] == "alice@example.com")
    check("reply subject prefixed Re:", send_args["subject"] == "Re: Meeting?")
    check("reply carries thread_id", send_args.get("thread_id") == "t1")
    check("reply result ok", rep["status"] == "ok")

    print("6) loop gate on a REAL destructive tool (gmail_send)")
    fake.calls.clear()
    loop.chat = scripted_chat([
        _msg(tool_calls=[_tool_call("s1", "gmail_send",
                                    '{"to":"boss@x.com","subject":"Hi","body":"hello"}')]),
        _msg(content="Want me to send that to boss@x.com? Confirm and I will."),
    ])
    await loop.run_turn("email boss hello", [], conversation_id="c1")
    sent_calls = [s for s, _ in fake.calls if s == "GMAIL_SEND_EMAIL"]
    check("gmail_send NOT executed when proposed", sent_calls == [])

    # Approve: the STORED send args run before the model, which just narrates.
    loop.chat = scripted_chat([_msg(content="Sent.")])
    await loop.run_turn("yes send it", [], conversation_id="c1")
    sent_calls = [s for s, _ in fake.calls if s == "GMAIL_SEND_EMAIL"]
    check("gmail_send executed on approval", sent_calls == ["GMAIL_SEND_EMAIL"])

    _composio.set_client(None)
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
