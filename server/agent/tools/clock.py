"""The clock tool: get_current_time.

Trivial by design — it's the first real tool, here to prove the tool round-trip
(model -> tool call -> result -> reply) before the Composio tools land. It also
models the pattern every later tool follows: a Pydantic arg-model that generates
the advertised schema and validates incoming args, and a handler returning a
``ToolResult``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from config import settings
from schemas import ToolResult, ToolSpec


class GetCurrentTimeArgs(BaseModel):
    timezone: str | None = Field(
        default=None,
        description="IANA timezone name, e.g. 'Asia/Tokyo'. Omit for the user's home timezone.",
    )


def _upcoming_dates(today) -> dict[str, str]:
    """Ready-made resolution of relative day phrases to exact dates, so the model
    never has to do weekday arithmetic (its most common date slip). Each weekday
    name maps to its nearest upcoming occurrence within the next week."""
    out: dict[str, str] = {
        "today": today.isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
    }
    for offset in range(0, 8):  # today .. +7; nearest occurrence of each weekday wins
        day = today + timedelta(days=offset)
        name = day.strftime("%A")
        out.setdefault(name, day.isoformat())
    return out


def _get_current_time(timezone: str | None = None) -> ToolResult:
    tz_name = timezone or settings.HOME_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ToolResult.error(f"Unknown timezone: {tz_name!r}")
    now = datetime.now(tz)
    return ToolResult.ok(
        data={
            "timezone": tz_name,
            "iso": now.isoformat(),
            "date": now.date().isoformat(),
            "weekday": now.strftime("%A"),
            "human": now.strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
            # Resolve "Thursday" / "tomorrow" by lookup, not arithmetic.
            "upcoming": _upcoming_dates(now.date()),
        }
    )


SPEC = ToolSpec.from_model(
    name="get_current_time",
    description=(
        "Get the current date, time, and weekday (optionally in a specific IANA "
        "timezone). Also returns an 'upcoming' map resolving relative day phrases "
        "('tomorrow', weekday names) to exact dates — use it instead of computing "
        "dates yourself."
    ),
    args_model=GetCurrentTimeArgs,
    handler=_get_current_time,
    destructive=False,
)
