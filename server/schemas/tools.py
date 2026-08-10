"""Contracts for the tool layer: the model's requests, our results, and the
registry's tool definitions.

The transcript itself (the ``messages`` list) stays as plain dicts — that is the
OpenAI/OpenRouter wire format. What we type here is what crosses the boundary
between the loop and the tools.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


class ToolStatus(str, Enum):
    """Outcome of a tool call, as seen by the loop and (via JSON) the model."""

    OK = "ok"
    ERROR = "error"
    NEEDS_CONFIRMATION = "needs_confirmation"


class ToolResult(BaseModel):
    """Uniform return value for every tool.

    Serialized with ``model_dump()`` and fed back to the model as a tool message,
    so keep it small and JSON-friendly. Use the constructors, not the raw ctor.
    """

    status: ToolStatus
    data: Any | None = None
    note: str | None = None

    @classmethod
    def ok(cls, data: Any = None, note: str | None = None) -> "ToolResult":
        return cls(status=ToolStatus.OK, data=data, note=note)

    @classmethod
    def error(cls, note: str, data: Any = None) -> "ToolResult":
        return cls(status=ToolStatus.ERROR, note=note, data=data)

    @classmethod
    def needs_confirmation(cls, note: str) -> "ToolResult":
        return cls(status=ToolStatus.NEEDS_CONFIRMATION, note=note)


class ToolCall(BaseModel):
    """A typed view of one tool call the model requested.

    ``from_openai`` centralizes parsing the raw call dict — including tolerating
    the malformed ``arguments`` JSON that smaller models sometimes emit, so the
    loop never crashes on a bad tool call.
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_openai(cls, raw: dict) -> "ToolCall":
        fn = raw.get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        else:
            try:
                args = json.loads(raw_args or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        return cls(id=raw.get("id", ""), name=fn.get("name", ""), arguments=args)


class ToolSpec(BaseModel):
    """A registry entry: metadata + the callable that runs it.

    An optional ``args_model`` (a Pydantic model) is the single source of truth
    for a tool's inputs: it generates the advertised JSON Schema *and* validates
    the args the model sends back. Build one with ``from_model`` and you can't let
    the two drift. ``handler`` and ``args_model`` are excluded from serialization.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    # Unconditionally destructive (e.g. delete, send).
    destructive: bool = False
    # Conditionally destructive: a predicate over the call's args. Used for tools
    # that are only outward-facing for certain inputs — e.g. calendar_create is
    # destructive only when it invites attendees.
    destructive_when: Callable[[dict[str, Any]], bool] | None = Field(
        default=None, exclude=True, repr=False
    )
    handler: Callable[..., Any] = Field(exclude=True, repr=False)
    args_model: type[BaseModel] | None = Field(default=None, exclude=True, repr=False)

    @classmethod
    def from_model(
        cls,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: Callable[..., Any],
        destructive: bool = False,
        destructive_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> "ToolSpec":
        """Build a spec whose schema and validation both come from ``args_model``."""
        return cls(
            name=name,
            description=description,
            parameters=args_model.model_json_schema(),
            args_model=args_model,
            handler=handler,
            destructive=destructive,
            destructive_when=destructive_when,
        )

    def is_destructive(self, args: dict[str, Any]) -> bool:
        """Whether *this* call needs the gate: unconditional, or the predicate."""
        if self.destructive:
            return True
        if self.destructive_when is not None:
            try:
                return bool(self.destructive_when(args))
            except Exception:
                # If the predicate can't decide, fail safe -> treat as destructive.
                return True
        return False

    def validate_args(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Coerce/validate raw args against the arg-model (no-op if none)."""
        if self.args_model is None:
            return dict(raw)
        return self.args_model(**raw).model_dump(exclude_none=True)

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
