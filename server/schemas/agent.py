"""Contracts for the agent loop itself."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(str, Enum):
    """Why ``run_turn`` returned."""

    COMPLETED = "completed"  # model produced a plain text reply
    MAX_ITERS = "max_iters"  # hit the tool-call budget first


class TurnResult(BaseModel):
    """What one turn of the loop returns.

    ``history`` is the full updated transcript (plain dicts) to feed into the
    next turn. The rest is for the caller/observability.
    """

    reply: str
    history: list[dict] = Field(default_factory=list)
    iterations: int = 0
    tools_used: list[str] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.COMPLETED
