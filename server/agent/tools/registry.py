"""Tool registry: the single place tools are registered, advertised, and run.

Add a tool by importing its ``SPEC`` and listing it in ``_SPECS``. Everything
else — the schemas advertised to the model, the name->spec lookup, and the set of
destructive tools — is derived from that list, so there's no second place to keep
in sync.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from pydantic import ValidationError

from schemas import ToolResult, ToolSpec

from .calendar import SPECS as CALENDAR_SPECS
from .clock import SPEC as CLOCK_SPEC
from .gmail import SPECS as GMAIL_SPECS
from .web_search import SPEC as WEB_SEARCH_SPEC

# --- registration --------------------------------------------------------
_SPECS: list[ToolSpec] = [
    CLOCK_SPEC,
    *CALENDAR_SPECS,
    *GMAIL_SPECS,
    WEB_SEARCH_SPEC,
]

TOOLS: dict[str, ToolSpec] = {s.name: s for s in _SPECS}
TOOL_SCHEMAS: list[dict] = [s.to_openai() for s in _SPECS]
DESTRUCTIVE_TOOLS: set[str] = {s.name for s in _SPECS if s.destructive}


async def execute_tool(name: str, args: dict[str, Any]) -> dict:
    """Validate args, run the tool, and return a JSON-able ``ToolResult`` dict.

    Never raises: unknown tools, bad args, and handler exceptions all come back as
    an ``error`` result so the loop can hand it to the model instead of crashing.
    """
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult.error(f"Unknown tool: {name!r}").model_dump()

    try:
        validated = spec.validate_args(args)
    except ValidationError as exc:
        return ToolResult.error(f"Invalid arguments for {name}: {exc}").model_dump()

    try:
        if inspect.iscoroutinefunction(spec.handler):
            result = await spec.handler(**validated)
        else:
            # Sync handlers (e.g. Composio's blocking network calls) run in a
            # worker thread so they never freeze the event loop under the async
            # server. Harmless in the single-threaded REPL too.
            result = await asyncio.to_thread(spec.handler, **validated)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # tools shouldn't take down the loop
        return ToolResult.error(f"{name} failed: {exc}").model_dump()

    if isinstance(result, ToolResult):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return ToolResult.ok(data=result).model_dump()
