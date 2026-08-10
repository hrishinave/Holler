"""Proactive email monitor.

Polls the inbox for new unread mail and, for each message not seen before,
triages it with a lightweight model call: the model either writes a one-line
heads-up in the assistant's voice, or replies SKIP (silence = not worth a ping).
Important ones are pushed to the owner's Telegram chat; every processed id is
recorded so nothing gets flagged twice.

Off unless ``EMAIL_MONITOR_ENABLED`` — it sends unprompted messages about a real
inbox. ``poll`` / ``classify`` / ``send`` are injectable for tests.
"""

from __future__ import annotations

import asyncio

from agent.prompts import load_prompt
from agent.tools import _composio
from agent.tools import gmail as gmail_tool
from channels import telegram
from config import settings
from llm import chat
from proactivity import store as pstore
from schemas import EmailMessage

_TRIAGE = (
    "You triage the user's email inbox. You are shown ONE new message. If it "
    "genuinely warrants interrupting them — time-sensitive, personal, from a real "
    "person who needs a response, or otherwise important — write ONE short "
    "heads-up line in your voice. If it's a newsletter, promotion, receipt, "
    "automated notification, or otherwise not worth a ping, reply with exactly: SKIP"
)


def _owner_chat_id() -> str:
    if settings.OWNER_CHAT_ID:
        return settings.OWNER_CHAT_ID
    ids = [c.strip() for c in settings.TELEGRAM_ALLOWED_CHAT_IDS.split(",") if c.strip()]
    return ids[0] if ids else ""


def _default_poll() -> list[EmailMessage]:
    data = _composio.execute(
        "GMAIL_FETCH_EMAILS",
        {"query": "is:unread in:inbox newer_than:2d", "max_results": 10},
    )
    return [gmail_tool._norm_message(m, include_body=False) for m in gmail_tool._messages_from(data)]


async def _default_classify(msg: EmailMessage) -> str | None:
    sender = str(msg.sender) if msg.sender else "unknown"
    prompt = (
        f"New email:\nFrom: {sender}\nSubject: {msg.subject or '(none)'}\n"
        f"Preview: {msg.snippet or ''}"
    )
    system = load_prompt("voice") + "\n\n" + _TRIAGE
    resp = await chat([{"role": "user", "content": prompt}], system=system)
    reply = (resp["choices"][0]["message"].get("content") or "").strip()
    if not reply or reply.upper().startswith("SKIP"):
        return None
    return reply


class EmailMonitor:
    def __init__(self, *, interval: float | None = None, owner_chat_id: str | None = None,
                 poll=None, classify=None, send=None):
        self.interval = interval if interval is not None else settings.EMAIL_POLL_SECONDS
        self.owner_chat_id = owner_chat_id if owner_chat_id is not None else _owner_chat_id()
        self._poll = poll or _default_poll
        self._classify = classify or _default_classify
        self._send = send or telegram.send
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                print("email monitor tick error:", exc, flush=True)
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        """Poll once and nudge on anything new + important. Safe to call in tests."""
        owner = self.owner_chat_id
        if not owner:
            return  # not configured -> no-op
        messages = await asyncio.to_thread(self._poll)  # poll is sync (network)
        for msg in messages:
            if not msg.id or pstore.email_seen(msg.id):
                continue
            nudge: str | None = None
            try:
                nudge = await self._classify(msg)
            except Exception as exc:
                print("email classify error:", exc, flush=True)
            if nudge:
                await self._send(owner, nudge)
            pstore.mark_email(msg.id, bool(nudge))
