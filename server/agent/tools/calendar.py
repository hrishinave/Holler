"""Google Calendar tools (Composio-backed): calendar_list / create / delete.

Ported from autoagent's gcal composio_ops.py, adapted to emit our Pydantic
schemas and the ToolResult contract. The tricky bits are preserved verbatim in
spirit: ``_normalize_start`` (apply the timezone exactly once), create-takes-a-
duration (not an end instant), and ``_single_event`` (create nests the event
under ``response_data``).

Timezone auto-detect (tier 2 of the strategy): the calendar's own ``timeZone``
rides along on any events.list response, so we cache it from there — no extra
API call, no unverified slug — and fall back to HOME_TIMEZONE.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from config import settings
from schemas import Attendee, CalendarEvent, EventDateTime, ToolResult, ToolSpec

from . import _composio
from ._composio import MAX_DESCRIPTION_CHARS, MAX_LIST_RESULTS, first

_PRIMARY = "primary"

# ─── timezone auto-detect ────────────────────────────────────────────────────

_cached_tz: str | None = None


def _cache_tz_from_list(data: dict) -> None:
    global _cached_tz
    if _cached_tz:
        return
    tz = data.get("timeZone") or data.get("timezone")
    if isinstance(tz, str) and tz:
        _cached_tz = tz


def resolve_timezone() -> str:
    """The user's calendar timezone (cached), falling back to HOME_TIMEZONE."""
    global _cached_tz
    if _cached_tz:
        return _cached_tz
    try:
        data = _composio.execute(
            "GOOGLECALENDAR_EVENTS_LIST",
            {"calendar_id": _PRIMARY, "max_results": 1, "single_events": True},
        )
        _cache_tz_from_list(data)
    except Exception:
        pass
    return _cached_tz or settings.HOME_TIMEZONE


# ─── normalization ───────────────────────────────────────────────────────────


def _edge(event: dict, side: str) -> EventDateTime:
    """One event endpoint -> EventDateTime (all-day ``date`` vs timed ``dateTime``)."""
    edge = event.get(side)
    if isinstance(edge, dict):
        dt = first(edge, "dateTime", "datetime")
        date = first(edge, "date")
        tz = first(edge, "timeZone", "timezone") or None
        if dt:
            return EventDateTime(date_time=dt, time_zone=tz)
        if date:
            return EventDateTime(date=date, time_zone=tz)
    if isinstance(edge, str) and edge:
        return _wrap_edge_str(edge)
    flat = first(event, f"{side}Time", f"{side}_time")
    return _wrap_edge_str(flat) if flat else EventDateTime()


def _wrap_edge_str(value: str) -> EventDateTime:
    if len(value) == 10 and "T" not in value:  # YYYY-MM-DD => all-day
        return EventDateTime(date=value)
    return EventDateTime(date_time=value)


def _attendees(event: dict) -> list[Attendee]:
    raw = event.get("attendees")
    if not isinstance(raw, list):
        return []
    out: list[Attendee] = []
    for att in raw:
        if isinstance(att, dict):
            email = first(att, "email")
            if email:
                out.append(
                    Attendee(
                        email=email,
                        display_name=first(att, "displayName") or None,
                        response_status=first(att, "responseStatus") or None,
                    )
                )
        elif isinstance(att, str) and att:
            out.append(Attendee(email=att))
    return out


def _norm_event(event: dict) -> CalendarEvent:
    organizer = event.get("organizer")
    organizer_email = first(organizer, "email") if isinstance(organizer, dict) else (organizer or None)
    description = first(event, "description", "summaryOverride")
    description = description[:MAX_DESCRIPTION_CHARS] if isinstance(description, str) else None
    return CalendarEvent(
        id=first(event, "id", "eventId", "event_id") or None,
        summary=first(event, "summary", "title") or None,
        description=description,
        location=first(event, "location") or None,
        status=first(event, "status") or None,
        start=_edge(event, "start"),
        end=_edge(event, "end"),
        attendees=_attendees(event),
        organizer=organizer_email or None,
        html_link=first(event, "htmlLink", "html_link", "link") or None,
    )


def _events_from(data: dict) -> list[dict]:
    for key in ("items", "events", "response_data", "data", "result"):
        value = data.get(key)
        if isinstance(value, list):
            return [e for e in value if isinstance(e, dict)]
    for key in ("data", "response_data"):
        nested = data.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("items"), list):
            return [e for e in nested["items"] if isinstance(e, dict)]
    return []


def _single_event(data: dict) -> dict:
    """Pull the one event out of a CREATE_EVENT response (nested under response_data)."""
    for key in ("response_data", "data"):
        inner = data.get(key)
        if isinstance(inner, dict) and (inner.get("id") or inner.get("start")):
            return inner
    listed = _events_from(data)
    return listed[0] if listed else data


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_start(start: str, timezone: str | None) -> tuple[str, str | None]:
    """Emit a naive wall-clock in ``timezone`` so Composio applies the zone once.

    Composio's CREATE_EVENT treats ``start_datetime`` as naive local wall-clock and
    relabels it with ``timezone``. An offset-aware start + timezone double-applies,
    so we convert an offset-aware start into the target zone and strip the offset.
    """
    parsed = _parse_iso(start)
    if parsed is None or parsed.tzinfo is None:
        return start, timezone
    zone_name = timezone or "UTC"
    try:
        target = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        target, zone_name = ZoneInfo("UTC"), "UTC"
    return parsed.astimezone(target).strftime("%Y-%m-%dT%H:%M:%S"), zone_name


def _duration_parts(start: str, end: str | None) -> tuple[int, int]:
    """(hours, minutes) between start and end; default one hour."""
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end) if end else None
    if start_dt is None or end_dt is None or end_dt <= start_dt:
        return 1, 0
    total = int((end_dt - start_dt).total_seconds() // 60)
    return total // 60, total % 60


# ─── tools ───────────────────────────────────────────────────────────────────


class CalendarListArgs(BaseModel):
    time_min: str | None = Field(
        None, description="ISO-8601 lower bound, inclusive (e.g. 2026-08-10T00:00:00-05:00)."
    )
    time_max: str | None = Field(None, description="ISO-8601 upper bound, exclusive.")
    query: str | None = Field(None, description="Free-text filter over event fields.")
    max_results: int = Field(10, description="Max events to return (1-25).")


class CalendarCreateArgs(BaseModel):
    summary: str = Field(..., description="Event title.")
    start: str = Field(..., description="ISO-8601 start (e.g. 2026-08-11T14:00:00).")
    end: str | None = Field(None, description="ISO-8601 end; defaults to one hour after start.")
    description: str = ""
    location: str = ""
    attendees: list[str] = Field(
        default_factory=list,
        description="Attendee emails. Adding any sends them calendar invites.",
    )
    timezone: str | None = Field(
        None, description="IANA timezone for the start; defaults to the user's calendar timezone."
    )


class CalendarDeleteArgs(BaseModel):
    event_id: str = Field(..., description="Id of the event to delete.")


def _calendar_list(
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 10,
) -> ToolResult:
    n = max(1, min(int(max_results), MAX_LIST_RESULTS))
    args = {
        k: v
        for k, v in {
            "calendar_id": _PRIMARY,
            "timeMin": time_min,
            "timeMax": time_max,
            "max_results": n,
            "query": query,
            "single_events": True,
            "order_by": "startTime",
        }.items()
        if v not in (None, "")
    }
    data = _composio.execute("GOOGLECALENDAR_EVENTS_LIST", args)
    _cache_tz_from_list(data)
    events = [_norm_event(e).model_dump() for e in _events_from(data)]
    return ToolResult.ok(data={"count": len(events), "events": events})


def _calendar_create(
    summary: str,
    start: str,
    end: str | None = None,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    timezone: str | None = None,
) -> ToolResult:
    attendees = attendees or []
    duration_hour, duration_minutes = _duration_parts(start, end)
    send_start, send_tz = _normalize_start(start, timezone or resolve_timezone())
    args = {
        k: v
        for k, v in {
            "summary": summary,
            "start_datetime": send_start,
            "event_duration_hour": duration_hour,
            "event_duration_minutes": duration_minutes,
            "description": description,
            "location": location,
            "attendees": attendees or None,
            "calendar_id": _PRIMARY,
            "timezone": send_tz,
        }.items()
        if v not in (None, "")
    }
    data = _composio.execute("GOOGLECALENDAR_CREATE_EVENT", args)
    event = _norm_event(_single_event(data))
    who = f" (invited {len(attendees)})" if attendees else ""
    return ToolResult.ok(
        data={"created": bool(event.id), "event": event.model_dump()},
        note=f"Created '{event.summary or summary}'{who}.",
    )


def _calendar_delete(event_id: str) -> ToolResult:
    _composio.execute("GOOGLECALENDAR_DELETE_EVENT", {"event_id": event_id, "calendar_id": _PRIMARY})
    return ToolResult.ok(data={"deleted": True, "event_id": event_id}, note="Deleted.")


CALENDAR_LIST_SPEC = ToolSpec.from_model(
    name="calendar_list",
    description="List or search the user's Google Calendar events within a time window.",
    args_model=CalendarListArgs,
    handler=_calendar_list,
)
CALENDAR_CREATE_SPEC = ToolSpec.from_model(
    name="calendar_create",
    description="Create a calendar event. Adding attendees sends them invites (confirm first).",
    args_model=CalendarCreateArgs,
    handler=_calendar_create,
    destructive_when=lambda a: bool(a.get("attendees")),
)
CALENDAR_DELETE_SPEC = ToolSpec.from_model(
    name="calendar_delete",
    description="Delete a calendar event by its id.",
    args_model=CalendarDeleteArgs,
    handler=_calendar_delete,
    destructive=True,
)

SPECS = [CALENDAR_LIST_SPEC, CALENDAR_CREATE_SPEC, CALENDAR_DELETE_SPEC]
