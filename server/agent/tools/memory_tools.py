"""Semantic-memory tools: remember / forget / list_memory / email_rule.

These let the agent learn durable things about the user and persist hard email
rules. All operate on the user's own memory, so none are gated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from memory import facts
from schemas import ToolResult, ToolSpec


class RememberArgs(BaseModel):
    fact: str = Field(
        ..., description="A durable fact or preference about the user to remember, "
        "e.g. 'My manager is Priya', 'I don't care about security alerts', "
        "'I prefer morning workouts'."
    )


class ForgetArgs(BaseModel):
    fact_id: int = Field(..., description="Id of the fact to forget (from list_memory).")


class EmailRuleArgs(BaseModel):
    action: str = Field(..., description="'skip' to never flag matching email, 'flag' to always flag it.")
    pattern: str = Field(
        ..., description="Substring matched against the email's sender + subject, "
        "e.g. 'security alert', 'no-reply', 'priya@'."
    )


class NoArgs(BaseModel):
    pass


def _remember(fact: str) -> ToolResult:
    fact_id = facts.add_fact(fact)
    return ToolResult.ok(data={"id": fact_id}, note="Noted — I'll remember that.")


def _forget(fact_id: int) -> ToolResult:
    removed = facts.delete_fact(int(fact_id))
    return ToolResult.ok(data={"forgotten": removed}, note="Forgotten." if removed else "No such fact.")


def _list_memory() -> ToolResult:
    return ToolResult.ok(data={"facts": facts.list_facts(), "email_rules": facts.list_email_prefs()})


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
