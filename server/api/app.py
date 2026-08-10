"""FastAPI server: the always-on process.

Hosts the Telegram webhook and runs the proactivity scheduler as a background
task for its whole lifetime. Same ``run_turn`` engine the REPL uses.

Run:  uv --directory server run uvicorn api.app:app --port 8000
Set the webhook:  https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC_URL>/telegram/webhook

NOTE: proactivity only fires while this process is up — it needs an always-on
host (a laptop that sleeps = no 9pm reminder).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from channels import telegram
from config import settings
from proactivity.email_monitor import EmailMonitor
from proactivity.scheduler import Scheduler

scheduler = Scheduler()
email_monitor = EmailMonitor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await scheduler.start()  # begin firing due triggers
    if settings.EMAIL_MONITOR_ENABLED:
        await email_monitor.start()  # begin watching the inbox
    try:
        yield
    finally:
        await scheduler.stop()
        await email_monitor.stop()


app = FastAPI(title="personal-agent", lifespan=lifespan)


@app.get("/")
async def health() -> dict:
    return {"status": "ok", "model": settings.MODEL}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    update = await request.json()
    await telegram.handle_update(update)
    # Always 200 quickly so Telegram doesn't retry; errors are handled inside.
    return {"ok": True}
