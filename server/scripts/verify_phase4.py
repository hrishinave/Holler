"""Phase 4 verification: Telegram channel + FastAPI wiring, deterministically
(no real Telegram, no real model). Proves markdown cleaning, update parsing, the
allowlist, and the handle_update controller (memory + gate + send).

Run:  uv --directory server run python scripts/verify_phase4.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import channels.telegram as tg  # noqa: E402
from config import settings  # noqa: E402
from memory import store  # noqa: E402
from schemas import TurnResult  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _update(chat_id, text):
    return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": chat_id, "type": "private"}, "text": text}}


async def main():
    store.set_connection(sqlite3.connect(":memory:"))

    print("1) markdown -> clean telegram text")
    cleaned = tg._to_telegram_text("## Plan\n- **buy** milk\n- `code` and [site](https://x.com)")
    check("header stripped", "Plan" in cleaned and "#" not in cleaned)
    check("bullets -> •", "• buy milk" in cleaned)
    check("bold markers removed", "**" not in cleaned)
    check("link rendered as text (url)", "site (https://x.com)" in cleaned)
    check("backticks removed", "`" not in cleaned)

    print("2) update parsing")
    check("extracts (chat_id, text)", tg.extract(_update(123, "hi")) == ("123", "hi"))
    check("non-message -> None", tg.extract({"update_id": 5}) is None)
    check("no text -> None", tg.extract({"message": {"chat": {"id": 1}}}) is None)

    print("3) allowlist")
    settings.TELEGRAM_ALLOWED_CHAT_IDS = ""
    check("empty allowlist = open", tg._allowed("999") is True)
    settings.TELEGRAM_ALLOWED_CHAT_IDS = "111, 222"
    check("listed id allowed", tg._allowed("222") is True)
    check("unlisted id denied", tg._allowed("999") is False)

    # --- stub the model turn + the outbound send, keep everything else real ---
    sent: list[tuple] = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))
        return {"ok": True}

    captured: list[dict] = []

    async def fake_run_turn(text, history, *, authorized_destructive=False):
        captured.append({"text": text, "hist_len": len(history), "auth": authorized_destructive})
        new = list(history) + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": f"echo: {text}"},
        ]
        return TurnResult(reply=f"echo: {text}", history=new, iterations=1)

    tg.send = fake_send
    tg.run_turn = fake_run_turn

    print("4) handle_update controller (allowed chat)")
    settings.TELEGRAM_ALLOWED_CHAT_IDS = "123"
    await tg.handle_update(_update(123, "what's up"))
    check("reply sent back to chat", sent and sent[-1][0] == "123" and "echo: what's up" in sent[-1][1])
    check("turn ran once", len(captured) == 1)
    check("not authorized on a plain message", captured[-1]["auth"] is False)
    check("memory persisted per chat", store.count("tg:123") == 2)

    print("5) gate authorization flows from message text")
    await tg.handle_update(_update(123, "yes send it"))
    check("approval word -> authorized_destructive", captured[-1]["auth"] is True)

    print("6) per-chat memory isolation + resume")
    await tg.handle_update(_update(123, "again"))
    check("history grows for chat 123", captured[-1]["hist_len"] == 4)
    check("other chat starts empty", store.count("tg:999") == 0)

    print("7) disallowed chat is refused, no turn")
    before = len(captured)
    await tg.handle_update(_update(999, "let me in"))
    check("refusal sent", sent[-1][0] == "999" and "isn't available" in sent[-1][1])
    check("no turn ran for disallowed chat", len(captured) == before)

    print("8) FastAPI app wiring")
    from fastapi.testclient import TestClient
    from api.app import app

    client = TestClient(app)
    r = client.get("/")
    check("health endpoint 200", r.status_code == 200 and r.json()["status"] == "ok")
    routes = {getattr(rt, "path", None) for rt in app.routes}
    check("webhook route registered", "/telegram/webhook" in routes)

    settings.TELEGRAM_ALLOWED_CHAT_IDS = ""  # reset
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
