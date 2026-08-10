"""Verification for the proactive event envelope + notifier choke point:
routing, dedup, the outbox log, and silence on empty/unroutable. Deterministic.

Run:  uv --directory server run python scripts/verify_events.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proactivity import notifier  # noqa: E402
from proactivity import store as pstore  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


async def main():
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    sent = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    print("1) routes to the right chat + logs to outbox")
    ev = notifier.make_event("trigger", "tg:555", "go climbing", dedup_key="trigger:1:x")
    delivered = await notifier.deliver(ev, send=fake_send)
    check("delivered True", delivered is True)
    check("sent to chat 555", sent and sent[-1] == ("555", "go climbing"))
    check("recorded in outbox", pstore.outbox_recent()[0]["content"] == "go climbing")
    check("outbox marked delivered", pstore.outbox_recent()[0]["delivered"] == 1)

    print("2) dedup: same key not delivered twice")
    again = await notifier.deliver(notifier.make_event("trigger", "tg:555", "go climbing", dedup_key="trigger:1:x"),
                                   send=fake_send)
    check("second identical event skipped", again is False)
    check("no extra send", len(sent) == 1)

    print("3) empty content is dropped")
    empty = await notifier.deliver(notifier.make_event("email", "tg:555", "   "), send=fake_send)
    check("empty not delivered", empty is False and len(sent) == 1)

    print("4) unroutable conversation is logged but not sent")
    repl_ev = notifier.make_event("trigger", "repl", "hello", dedup_key="repl:1")
    routed = await notifier.deliver(repl_ev, send=fake_send)
    check("repl conversation -> not delivered", routed is False)
    check("still recorded in outbox (delivered=0)",
          any(r["conversation_id"] == "repl" and r["delivered"] == 0 for r in pstore.outbox_recent()))
    check("no send for unroutable", len(sent) == 1)

    print("5) distinct keys both deliver")
    await notifier.deliver(notifier.make_event("email", "tg:999", "hi", dedup_key="email:a"), send=fake_send)
    await notifier.deliver(notifier.make_event("email", "tg:999", "yo", dedup_key="email:b"), send=fake_send)
    check("two distinct events both sent", len(sent) == 3)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
