"""Verification for the chill email follow-up: flagging an email schedules ONE
future follow-up trigger (unless disabled), and the follow-up prompt is handled-
aware (stay silent if dealt with) and low-pressure. Deterministic — no model,
no network.

Run:  uv --directory server run python scripts/verify_followup.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proactivity.email_monitor as em  # noqa: E402
from config import settings  # noqa: E402
from proactivity import store as pstore  # noqa: E402
from schemas import EmailAddress, EmailMessage  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _monitor(sent, classify_ret):
    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    async def fake_classify(msg):
        return classify_ret

    return em.EmailMonitor(owner_chat_id="123", poll=lambda: _inbox(), classify=fake_classify, send=fake_send)


def _inbox():
    return [EmailMessage(id="m1", sender=EmailAddress(email="sam@x.com", name="Sam"),
                         subject="Contract review", snippet="need your sign-off")]


async def main():
    settings.EMAIL_FOLLOWUP_HOURS = 3.0

    print("1) a flagged email schedules exactly one future follow-up")
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    sent = []
    await _monitor(sent, "Sam needs your sign-off on the contract").tick()
    check("the nudge was sent", len(sent) == 1)
    triggers = pstore.due("2999-01-01T00:00:00")  # everything up to far future
    check("one follow-up trigger created", len(triggers) == 1)
    t = triggers[0]
    check("scheduled to the owner's chat", t.conversation_id == "tg:123")
    check("scheduled in the future (not due now)", t.next_trigger > pstore.utcnow_iso())
    check("one-shot (no repeat)", t.repeat is None)

    print("2) the follow-up prompt is handled-aware and chill")
    p = t.prompt.lower()
    check("references the specific email", "sam" in p and "contract review" in p)
    check("tells it to stay silent if handled", "empty text" in p or "say nothing" in p)
    check("asks it to check read/replied state", "read" in p and "repl" in p)
    check("insists on low-pressure tone", "chill" in p or "low-pressure" in p or "nagg" in p)

    print("3) a non-notified email schedules no follow-up")
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    sent2 = []
    await _monitor(sent2, None).tick()  # classify returns None -> no nudge
    check("nothing sent", sent2 == [])
    check("no follow-up scheduled", pstore.due("2999-01-01T00:00:00") == [])

    print("4) follow-ups can be disabled (EMAIL_FOLLOWUP_HOURS=0)")
    settings.EMAIL_FOLLOWUP_HOURS = 0
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    sent3 = []
    await _monitor(sent3, "Sam needs your sign-off").tick()
    check("nudge still sent", len(sent3) == 1)
    check("no follow-up when disabled", pstore.due("2999-01-01T00:00:00") == [])
    settings.EMAIL_FOLLOWUP_HOURS = 3.0  # restore

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
