"""Contracts for the destructive-action approval gate.

A ``PendingAction`` is a destructive tool call the model proposed and the user
has not yet approved. It is persisted (SQLite) so it survives between the turn
that proposes it and the later turn that approves it — ``run_turn`` is stateless
per call, so the pending record is the only thing that carries the intent across.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PendingStatus(str, Enum):
    PENDING = "pending"        # awaiting the user's go-ahead
    CONSUMED = "consumed"      # approved and executed (single-use)
    SUPERSEDED = "superseded"  # replaced by a newer proposal
    EXPIRED = "expired"        # TTL elapsed before approval


class PendingAction(BaseModel):
    """One destructive action, frozen with the exact args that were shown.

    Approval executes *these* stored ``arguments`` verbatim — the model never gets
    to regenerate them after review, so "what you approved is what runs."
    """

    id: str
    conversation_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    preview: str = ""
    status: PendingStatus = PendingStatus.PENDING
    created_at: str
    expires_at: str
