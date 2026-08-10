"""Proactivity tools: trigger_create / trigger_list / trigger_cancel.

These let the agent schedule itself — "remind me at 6pm", "every morning
summarize my day". A trigger stores a natural-language *task* that gets run
through the loop when it fires (so it can be a reminder OR something richer like
"check my calendar and tell me what's next"). All read/write local scheduling
state only, so none are gated.

The target conversation comes from the ContextVar the channel set for this turn
(``context.get_conversation``), so the reminder goes back to the right chat.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from config import settings
from context import get_conversation
from proactivity import store as pstore
from schemas import ToolResult, ToolSpec

_REPEATS = {"hourly", "daily", "weekly"}


def _to_utc_iso(when: str) -> str:
    """Parse an ISO-8601 time to naive UTC. Naive input is read in HOME_TIMEZONE."""
    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(settings.HOME_TIMEZONE))
        except (ZoneInfoNotFoundError, ValueError):
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class TriggerCreateArgs(BaseModel):
    when: str = Field(
        ..., description="When to fire, ISO-8601 (e.g. 2026-08-11T18:00:00). Compute the "
        "absolute time yourself from the current time; naive times are read in the user's timezone."
    )
    task: str = Field(
        ..., description="What to do/say when it fires, e.g. 'Remind them to call mom' or "
        "'Summarize today's calendar'."
    )
    repeat: str | None = Field(
        None, description="Optional recurrence: 'hourly', 'daily', or 'weekly'. Omit for one-shot."
    )


class NoArgs(BaseModel):
    pass


class TriggerCancelArgs(BaseModel):
    trigger_id: int = Field(..., description="Id of the reminder to cancel.")


def _trigger_create(when: str, task: str, repeat: str | None = None) -> ToolResult:
    conversation_id = get_conversation()
    if not conversation_id:
        return ToolResult.error("No conversation to attach this reminder to.")
    if repeat and repeat not in _REPEATS:
        return ToolResult.error("repeat must be one of: hourly, daily, weekly.")
    try:
        next_trigger = _to_utc_iso(when)
    except ValueError:
        return ToolResult.error(f"Couldn't parse time {when!r}. Use ISO-8601.")
    trig = pstore.create(conversation_id, task, next_trigger, repeat)
    kind = f" (repeats {repeat})" if repeat else ""
    return ToolResult.ok(data=trig.model_dump(), note=f"Scheduled #{trig.id} for {next_trigger} UTC{kind}.")


def _trigger_list() -> ToolResult:
    conversation_id = get_conversation()
    trigs = pstore.list_for(conversation_id, active_only=True)
    return ToolResult.ok(data={"count": len(trigs), "triggers": [t.model_dump() for t in trigs]})


def _trigger_cancel(trigger_id: int) -> ToolResult:
    cancelled = pstore.cancel(int(trigger_id))
    return ToolResult.ok(
        data={"cancelled": cancelled},
        note="Cancelled." if cancelled else "No active reminder with that id.",
    )


TRIGGER_CREATE_SPEC = ToolSpec.from_model(
    name="trigger_create",
    description="Schedule a reminder or recurring task to run later and message the user.",
    args_model=TriggerCreateArgs,
    handler=_trigger_create,
)
TRIGGER_LIST_SPEC = ToolSpec.from_model(
    name="trigger_list",
    description="List the user's active scheduled reminders/tasks.",
    args_model=NoArgs,
    handler=_trigger_list,
)
TRIGGER_CANCEL_SPEC = ToolSpec.from_model(
    name="trigger_cancel",
    description="Cancel a scheduled reminder by its id.",
    args_model=TriggerCancelArgs,
    handler=_trigger_cancel,
)

SPECS = [TRIGGER_CREATE_SPEC, TRIGGER_LIST_SPEC, TRIGGER_CANCEL_SPEC]
