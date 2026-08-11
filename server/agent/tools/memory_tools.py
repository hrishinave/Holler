"""Semantic-memory tools: remember / forget / list_memory / email_rule.

These let the agent learn durable things about the user and persist hard email
rules. All operate on the user's own memory, so none are gated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from memory import facts
from schemas import MemoryKind, MemorySource, MemoryStrength, ToolResult, ToolSpec


class RememberArgs(BaseModel):
    fact: str = Field(
        ..., description="A durable fact or preference the user stated, e.g. "
        "'My manager is Priya', 'I prefer morning workouts'."
    )
    kind: str = Field(
        "fact", description="One of: identity, relationship, preference, constraint, "
        "routine, fact. Pick the closest.",
    )
    hard_rule: bool = Field(
        False, description="True only for an explicit hard rule the user set "
        "('never schedule me before 10'). Otherwise it's a preference.",
    )
    canonical_key: str = Field(
        "", description="Reuse a stable dotted key (e.g. 'relationship.manager') "
        "when CORRECTING an earlier belief on the same topic, so it replaces the old "
        "one instead of adding a duplicate. Leave empty for a brand-new fact.",
    )


class ForgetArgs(BaseModel):
    fact_id: int = Field(..., description="Id of the memory to forget (from list_memory).")


class EmailRuleArgs(BaseModel):
    action: str = Field(..., description="'skip' to never flag matching email, 'flag' to always flag it.")
    pattern: str = Field(
        ..., description="Substring matched against the email's sender + subject, "
        "e.g. 'security alert', 'no-reply', 'priya@'."
    )


class NoArgs(BaseModel):
    pass


def _coerce_kind(kind: str) -> MemoryKind:
    try:
        return MemoryKind(kind)
    except ValueError:
        return MemoryKind.FACT


def _remember(fact: str, kind: str = "fact", hard_rule: bool = False, canonical_key: str = "") -> ToolResult:
    key = canonical_key.strip() or None
    # A keyed remember is the user correcting/replacing a prior belief on that topic.
    source = MemorySource.CORRECTED if key else MemorySource.EXPLICIT
    strength = MemoryStrength.HARD_CONSTRAINT if hard_rule else MemoryStrength.PREFERENCE
    result = facts.add_memory(
        fact, kind=_coerce_kind(kind), source=source, strength=strength, canonical_key=key,
    )
    if not result.get("stored"):
        return ToolResult.ok(data=result, note="Already knew that.")
    note = "Updated — replaced what I had." if result.get("superseded_id") else "Noted — I'll remember that."
    return ToolResult.ok(data={"id": result["id"]}, note=note)


def _forget(fact_id: int) -> ToolResult:
    removed = facts.delete_memory(int(fact_id))
    return ToolResult.ok(data={"forgotten": removed}, note="Forgotten." if removed else "No such memory.")


def _list_memory() -> ToolResult:
    return ToolResult.ok(data={"memories": facts.list_memories(), "email_rules": facts.list_email_prefs()})


def _email_rule(action: str, pattern: str) -> ToolResult:
    if action not in ("skip", "flag"):
        return ToolResult.error("action must be 'skip' or 'flag'.")
    rule_id = facts.add_email_pref(action, pattern)
    verb = "never flag" if action == "skip" else "always flag"
    return ToolResult.ok(data={"id": rule_id}, note=f"Done — I'll {verb} email matching {pattern!r}.")


REMEMBER_SPEC = ToolSpec.from_model(
    name="remember",
    description="Remember a durable fact or preference about the user (persists across all chats).",
    args_model=RememberArgs,
    handler=_remember,
)
FORGET_SPEC = ToolSpec.from_model(
    name="forget",
    description="Forget a stored fact by its id.",
    args_model=ForgetArgs,
    handler=_forget,
)
LIST_MEMORY_SPEC = ToolSpec.from_model(
    name="list_memory",
    description="List what you remember about the user (facts + email rules).",
    args_model=NoArgs,
    handler=_list_memory,
)
EMAIL_RULE_SPEC = ToolSpec.from_model(
    name="email_rule",
    description="Add a hard email rule to always/never flag mail matching a sender/subject pattern.",
    args_model=EmailRuleArgs,
    handler=_email_rule,
)

SPECS = [REMEMBER_SPEC, FORGET_SPEC, LIST_MEMORY_SPEC, EMAIL_RULE_SPEC]
