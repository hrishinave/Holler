"""Proactive event contract.

Every message the assistant sends *unprompted* (a fired reminder, an email nudge,
future sources) is a ``ProactiveEvent``. All of them flow through one delivery
choke point (``proactivity/notifier.deliver``), which is where cross-cutting
concerns live: dedup, an outbox log, and — later — quiet hours and batching.
"""

from __future__ import annotations

from pydantic import BaseModel


class ProactiveEvent(BaseModel):
    source: str            # "trigger" | "email" | ...
    conversation_id: str   # who to notify, e.g. "tg:8962205869"
    content: str           # the message to send
    created_at: str        # naive UTC ISO
    # Stable key so the same event isn't delivered twice (None = never dedup).
    dedup_key: str | None = None
