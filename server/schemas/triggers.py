"""Proactivity contracts: a scheduled trigger."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class TriggerStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


class Trigger(BaseModel):
    """A scheduled task. When ``next_trigger`` (UTC) passes, the scheduler runs
    ``prompt`` through the loop for ``conversation_id`` and sends the reply.
    ``repeat`` (hourly/daily/weekly, or None) controls one-shot vs recurring.
    """

    id: int | None = None
    conversation_id: str
    prompt: str
    next_trigger: str  # naive UTC ISO, "YYYY-MM-DDTHH:MM:SS"
    repeat: str | None = None
    status: TriggerStatus = TriggerStatus.ACTIVE
    created_at: str | None = None
