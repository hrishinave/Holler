"""Normalized Google Calendar contracts (the shapes ``agent/tools/calendar.py``
emits). Finalized against the Composio port in Phase 2.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field


class EventDateTime(BaseModel):
    """Google's event endpoint: either an all-day ``date`` (YYYY-MM-DD) or a
    timed ``date_time`` (ISO 8601) with an optional IANA ``time_zone``.

    This split is what makes the timezone handling correct — see the CREATE_EVENT
    gotcha in CLAUDE.md (naive datetime + timezone arg, never offset + timezone).
    """

    date_time: str | None = None
    date: str | None = None
    time_zone: str | None = None

    @property
    def is_all_day(self) -> bool:
        return self.date is not None and self.date_time is None


class Attendee(BaseModel):
    email: str
    display_name: str | None = None
    # needsAction | accepted | declined | tentative
    response_status: str | None = None


class CalendarEvent(BaseModel):
    """A normalized calendar event."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: EventDateTime = Field(default_factory=EventDateTime)
    end: EventDateTime = Field(default_factory=EventDateTime)
    attendees: list[Attendee] = Field(default_factory=list)
    organizer: str | None = None
    html_link: str | None = None
    status: str | None = None  # confirmed | tentative | cancelled

    @computed_field
    @property
    def all_day(self) -> bool:
        return self.start.is_all_day


class CalendarInfo(BaseModel):
    """A calendar from the user's list — used to read the primary calendar's
    ``time_zone`` for auto-detect (tier 2 of the timezone strategy).
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    summary: str | None = None
    time_zone: str | None = None
    primary: bool = False
