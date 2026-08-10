"""Pydantic contracts shared across layers.

Organized by domain so each layer imports what it needs, while callers can still
do ``from schemas import ToolResult, CalendarEvent, ...``. Tool *argument* models
(one per tool) live with their tools, not here: they must track the ported
Composio signatures and each generates its own advertised JSON schema.
"""

from __future__ import annotations

from .agent import Role, StopReason, TurnResult
from .calendar import Attendee, CalendarEvent, CalendarInfo, EventDateTime
from .email import EmailAddress, EmailMessage, EmailThread, OutgoingEmail
from .tools import ToolCall, ToolResult, ToolSpec, ToolStatus
from .triggers import Trigger, TriggerStatus
from .web import WebSearchResponse, WebSearchResult

__all__ = [
    # tools
    "ToolStatus",
    "ToolResult",
    "ToolCall",
    "ToolSpec",
    # calendar
    "EventDateTime",
    "Attendee",
    "CalendarEvent",
    "CalendarInfo",
    # email
    "EmailAddress",
    "EmailMessage",
    "EmailThread",
    "OutgoingEmail",
    # agent
    "Role",
    "StopReason",
    "TurnResult",
    # web
    "WebSearchResult",
    "WebSearchResponse",
    # proactivity
    "Trigger",
    "TriggerStatus",
]
