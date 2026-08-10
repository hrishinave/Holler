"""Normalized Gmail contracts (the shapes ``agent/tools/gmail.py`` emits) plus
the outgoing-mail input contract. Finalized against the Composio port in Phase 2.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EmailAddress(BaseModel):
    """A parsed address from a header (``Alice <alice@x.com>``)."""

    email: str
    name: str | None = None

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>" if self.name else self.email


class EmailMessage(BaseModel):
    """A normalized Gmail message.

    ``sender`` maps the ``From`` header (``from`` is a Python keyword).
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    thread_id: str | None = None
    sender: EmailAddress | None = None
    to: list[EmailAddress] = Field(default_factory=list)
    cc: list[EmailAddress] = Field(default_factory=list)
    subject: str | None = None
    snippet: str | None = None
    date: str | None = None
    body: str | None = None
    labels: list[str] = Field(default_factory=list)
    unread: bool = False


class EmailAttentionCategory(str, Enum):
    """The small set of inbox signals the proactive monitor understands."""

    MEETING_REQUEST = "meeting_request"
    ACTION_REQUIRED = "action_required"
    DEADLINE = "deadline"
    PERSONAL = "personal"
    NOISE = "noise"


class EmailAttentionSignal(BaseModel):
    """Structured, evidence-bound interpretation of one incoming email."""

    category: EmailAttentionCategory
    should_notify: bool
    who: str | None = None
    request: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    deadline: str | None = None
    response_expected: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notable_context: str | None = None


class EmailThread(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    subject: str | None = None
    messages: list[EmailMessage] = Field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.messages)


class OutgoingEmail(BaseModel):
    """The input contract for sending / replying (gate-protected tools).

    ``reply_to_message_id`` set => this is a reply on that message's thread.
    """

    to: list[str]
    subject: str
    body: str
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to_message_id: str | None = None
