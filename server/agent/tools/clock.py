"""The clock tool: get_current_time.

Trivial by design — it's the first real tool, here to prove the tool round-trip
(model -> tool call -> result -> reply) before the Composio tools land. It also
models the pattern every later tool follows: a Pydantic arg-model that generates
the advertised schema and validates incoming args, and a handler returning a
``ToolResult``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from config import settings
from schemas import ToolResult, ToolSpec


class GetCurrentTimeArgs(BaseModel):
    timezone: str | None = Field(
        default=None,
        description="IANA timezone name, e.g. 'Asia/Tokyo'. Omit for the user's home timezone.",
    )


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
            "human": now.strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
        }
    )


SPEC = ToolSpec.from_model(
    name="get_current_time",
    description="Get the current date and time, optionally in a specific IANA timezone.",
    args_model=GetCurrentTimeArgs,
    handler=_get_current_time,
    destructive=False,
)
