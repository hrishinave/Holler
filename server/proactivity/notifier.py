"""The single delivery choke point for proactive messages.

Every unprompted message — a fired reminder, an email nudge, future sources —
becomes a ``ProactiveEvent`` and goes through ``deliver``. Having one path means
dedup, an outbox log, and (later) quiet hours / batching all live in one place
instead of being re-implemented per source.

``send`` is injectable so tests (and each source) can supply their own sender;
it defaults to Telegram.
"""

from __future__ import annotations

from datetime import datetime, timezone

from channels import telegram
from proactivity import store as pstore
from schemas import ProactiveEvent


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _chat_id(conversation_id: str) -> str | None:
    """Telegram chat id from a conversation id ('tg:<id>'); None for others."""
    if conversation_id.startswith("tg:"):
        return conversation_id[3:]
    return None


def make_event(source: str, conversation_id: str, content: str,
               dedup_key: str | None = None) -> ProactiveEvent:
    return ProactiveEvent(
        source=source,
        conversation_id=conversation_id,
        content=content,
        created_at=_utcnow_iso(),
        dedup_key=dedup_key,
    )


async def deliver(event: ProactiveEvent, *, send=None) -> bool:
    """Deliver a proactive event. Returns True if it was actually sent.

    Drops empties, skips already-delivered dedup keys, routes to the right chat,
    and records every attempt (delivered or not) in the outbox.
    """
    send = send or telegram.send
    if not event.content or not event.content.strip():
        return False
    if pstore.outbox_seen(event.dedup_key):
        return False  # already delivered this exact event

    chat_id = _chat_id(event.conversation_id)
    delivered = False
    if chat_id:
        await send(chat_id, event.content)
        delivered = True

    pstore.record_outbox(event.source, event.conversation_id, event.content,
                         event.dedup_key, delivered)
    return delivered
