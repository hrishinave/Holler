"""Phase 5b verification: the email monitor, deterministically (no Gmail, no
model, no Telegram — poll/fetch/classify/send are injectable).

Run:  uv --directory server run python scripts/verify_phase5b.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proactivity import store as pstore  # noqa: E402
from proactivity.email_monitor import EmailMonitor  # noqa: E402
from schemas import EmailAddress, EmailMessage  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


async def main():
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))

    print("1) processed-emails store")
    check("unseen initially", not pstore.email_seen("m1"))
    pstore.mark_email("m1", True)
    check("seen after mark", pstore.email_seen("m1"))

    print("2) monitor triage: nudge important, skip promo, ignore already-seen")
    inbox = [
        EmailMessage(id="a1", sender=EmailAddress(email="boss@x.com", name="Boss"),
                     subject="Deadline moved to today", snippet="need the draft by 3pm"),
        EmailMessage(id="a2", sender=EmailAddress(email="news@promo.com"),
                     subject="50% off everything!", snippet="huge sale ends tonight"),
        EmailMessage(id="m1", subject="already processed", snippet="x"),  # pre-seen above
    ]
    sent = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    async def fake_classify(msg):
        # "important" only if it looks like a real, time-sensitive message
        return f"Heads up — {msg.subject}" if "Deadline" in (msg.subject or "") else None

    mon = EmailMonitor(owner_chat_id="123", poll=lambda: inbox, classify=fake_classify, send=fake_send)
    await mon.tick()

    check("nudged once (the important one)", len(sent) == 1)
    check("nudge went to owner chat", sent and sent[0][0] == "123")
    check("nudge is about the deadline", "Deadline" in sent[0][1])
    check("did NOT nudge the promo", all("50%" not in t for _, t in sent))
    check("important marked seen", pstore.email_seen("a1"))
    check("promo marked seen (so it won't re-trigger)", pstore.email_seen("a2"))

    print("3) idempotent: a second tick sends nothing new")
    await mon.tick()
    check("no duplicate nudges", len(sent) == 1)

    print("4) new important mail on a later poll IS nudged")
    inbox.append(EmailMessage(id="a3", sender=EmailAddress(email="mom@x.com", name="Mom"),
                              subject="Deadline for the tickets", snippet="book tonight"))
    await mon.tick()
    check("new important mail nudged", len(sent) == 2 and sent[-1][1].endswith("tickets"))

    print("5) no owner configured -> no-op")
    mon_no_owner = EmailMonitor(owner_chat_id="", poll=lambda: inbox, classify=fake_classify, send=fake_send)
    before = len(sent)
    await mon_no_owner.tick()
    check("no owner -> nothing sent", len(sent) == before)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
