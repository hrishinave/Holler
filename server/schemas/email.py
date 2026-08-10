"""Normalized Gmail contracts (the shapes ``agent/tools/gmail.py`` emits) plus
the outgoing-mail input contract. Finalized against the Composio port in Phase 2.
"""

from __future__ import annotations

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
