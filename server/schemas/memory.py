"""Contracts for the semantic user model.

A memory is not a flat sentence — it's a small typed belief that knows *what kind*
of knowledge it is, *how it was learned* (you were told vs. the agent guessed),
*how strongly* it should steer behavior, and *when* it stops being true. Those
distinctions are what let the assistant apply what it knows with judgment instead
of reciting a notebook.

Deliberately lean: no numeric confidence (false precision on a small model) and no
behavioral-signal tracking yet — those come only if evidence shows they're needed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryKind(str, Enum):
    IDENTITY = "identity"          # who they are: name, city, timezone
    RELATIONSHIP = "relationship"  # people: manager, collaborator, family
    PREFERENCE = "preference"      # how they like things: terse email, afternoons
    CONSTRAINT = "constraint"      # hard limits: never over school pickup
    ROUTINE = "routine"            # recurring habits: gym Tuesday evenings
    FACT = "fact"                  # durable misc. fact that fits nowhere above


class MemorySource(str, Enum):
    EXPLICIT = "explicit"      # the user stated it outright
    CORRECTED = "corrected"    # the user fixed a previous belief
    INFERRED = "inferred"      # the agent guessed it from conversation


class MemoryStrength(str, Enum):
    HARD_CONSTRAINT = "hard_constraint"  # a rule; obey unless overridden
    PREFERENCE = "preference"            # a default to favor when practical
    HYPOTHESIS = "hypothesis"            # an unconfirmed guess; don't act on it


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


# How much a source is allowed to overwrite another on the same canonical key.
# What the user tells (or corrects) always outranks what the agent inferred.
SOURCE_AUTHORITY: dict[str, int] = {
    MemorySource.CORRECTED.value: 3,
    MemorySource.EXPLICIT.value: 2,
    MemorySource.INFERRED.value: 1,
}


class MemoryItem(BaseModel):
    """One persisted belief about the user."""

    # Dumped dicts carry plain string values ("inferred"), not enum members, so
    # the prompt block and tool output read cleanly and consistently.
    model_config = ConfigDict(use_enum_values=True)

    id: int | None = None
    # A stable dotted concept key so the same idea collapses to one slot, e.g.
    # "relationship.manager", "identity.name", "preference.meeting_time". Optional:
    # a loose fact may have none, and simply accumulates.
    canonical_key: str | None = None
    kind: MemoryKind
    content: str
    source: MemorySource
    strength: MemoryStrength
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes_id: int | None = None
    expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("content")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("memory content must be non-empty")
        return v.strip()


class MemoryProposal(BaseModel):
    """What reflection proposes for one observation, before it's persisted."""

    operation: str = Field(..., description="add | supersede | ignore")
    kind: MemoryKind
    canonical_key: str | None = None
    content: str = ""
    source: MemorySource
    strength: MemoryStrength
    expires_at: str | None = None
    reason: str = ""

    @field_validator("operation")
    @classmethod
    def _known_op(cls, v: str) -> str:
        if v not in ("add", "supersede", "ignore"):
            raise ValueError("operation must be add | supersede | ignore")
        return v

    def is_store(self) -> bool:
        return self.operation in ("add", "supersede") and bool(self.content.strip())
