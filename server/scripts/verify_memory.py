"""Verification for semantic memory: facts store, memory tools, prompt injection,
and the email monitor's hard-rule + facts-informed triage. Deterministic.

Run:  uv --directory server run python scripts/verify_memory.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import facts  # noqa: E402
from proactivity import store as pstore  # noqa: E402
from proactivity.email_monitor import EmailMonitor  # noqa: E402
from schemas import EmailAddress, EmailMessage  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


async def main():
    facts.set_connection(sqlite3.connect(":memory:", check_same_thread=False))
    pstore.set_connection(sqlite3.connect(":memory:", check_same_thread=False))

    print("1) facts store + tools")
    from agent.tools.registry import TOOLS, execute_tool
    for name in ("remember", "forget", "list_memory", "email_rule"):
        check(f"{name} registered", name in TOOLS)
    r = await execute_tool("remember", {"fact": "My manager is Priya"})
    fid = r["data"]["id"]
    check("remember stores a fact", any(f["content"] == "My manager is Priya" for f in facts.list_facts()))
    lm = await execute_tool("list_memory", {})
    check("list_memory returns the fact", any(f["content"] == "My manager is Priya" for f in lm["data"]["facts"]))

    print("2) facts injected into the system prompt")
    from agent.loop import system_prompt
    sp = system_prompt()
    check("system prompt includes the user fact", "Priya" in sp)
    check("system prompt has a 'what you know' block", "What you know about the user" in sp)

    print("3) hard email rules via email_rule tool")
    await execute_tool("email_rule", {"action": "skip", "pattern": "security alert"})
    await execute_tool("email_rule", {"action": "flag", "pattern": "priya@"})
    check("skip rule matches", facts.match_email_pref("no-reply@google.com", "Security alert for your account") == "skip")
    check("flag rule matches", facts.match_email_pref("priya@corp.com", "quick q") == "flag")
    check("no rule -> None", facts.match_email_pref("random@x.com", "hello") is None)
    check("skip wins over flag (deny-first)", facts.match_email_pref("priya@corp.com", "security alert") == "skip")

    print("4) forget removes a fact")
    await execute_tool("forget", {"fact_id": fid})
    check("fact gone after forget", not any(f["content"] == "My manager is Priya" for f in facts.list_facts()))

    print("5) email monitor honors hard rules (no model call needed)")
    inbox = [
        EmailMessage(id="e1", sender=EmailAddress(email="no-reply@google.com"),
                     subject="Security alert for your account", snippet="new login"),
        EmailMessage(id="e2", sender=EmailAddress(email="priya@corp.com", name="Priya"),
                     subject="re: the deck", snippet="can you send it"),
        EmailMessage(id="e3", sender=EmailAddress(email="rando@x.com"),
                     subject="hey", snippet="something"),
    ]
    sent = []
    classify_calls = []

    async def fake_send(chat_id, text):
        sent.append(text)

    async def fake_classify(msg):
        classify_calls.append(msg.id)
        return None  # LLM would skip e3

    mon = EmailMonitor(owner_chat_id="123", poll=lambda: inbox, classify=fake_classify, send=fake_send)
    await mon.tick()
    check("security alert SKIPPED by rule (not sent)", all("Security alert" not in t for t in sent))
    check("security alert never hit the model", "e1" not in classify_calls)
    check("priya FLAGGED by rule", any("Priya" in t for t in sent))
    check("priya never hit the model (hard flag)", "e2" not in classify_calls)
    check("ambiguous e3 went to the model", "e3" in classify_calls)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
