"""FastAPI server: the always-on process.

For now it hosts the Telegram webhook — the same ``run_turn`` engine the REPL
uses, just triggered by HTTP. Onboarding endpoints (Composio OAuth for the
client UI) and the proactivity startup tasks (Phase 5) slot in here later.

Run:  uv --directory server run uvicorn api.app:app --port 8000
Set the webhook:  https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC_URL>/telegram/webhook
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from channels import telegram
from config import settings

app = FastAPI(title="personal-agent")


@app.get("/")
async def health() -> dict:
    return {"status": "ok", "model": settings.MODEL}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    update = await request.json()
    await telegram.handle_update(update)
    # Always 200 quickly so Telegram doesn't retry; errors are handled inside.
    return {"ok": True}
