"""Gmail tools (Composio-backed): search / get / draft / send / reply.

Ported from autoagent's gmail composio_ops.py, adapted to emit our Pydantic
schemas. Header parsing is preserved: From/To/Subject/Date live in
``payload.headers`` (not top-level), with flattened fallbacks for other toolkit
versions.

Gate posture: ``gmail_draft`` is un-gated (nothing leaves the outbox);
``gmail_send`` and ``gmail_reply`` are unconditionally destructive.
"""

from __future__ import annotations

from email.utils import getaddresses, parseaddr

from pydantic import BaseModel, Field

from schemas import EmailAddress, EmailMessage, ToolResult, ToolSpec

from . import _composio
from ._composio import MAX_BODY_CHARS, MAX_LIST_RESULTS, first

# ─── normalization ───────────────────────────────────────────────────────────


def _headers_map(msg: dict) -> dict[str, str]:
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    headers = payload.get("headers") or []
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in headers
        if isinstance(h, dict)
    }


def _addr(value: str) -> EmailAddress | None:
    if not value:
        return None
    name, email = parseaddr(value)
    if not email:
        return None
    return EmailAddress(email=email, name=name or None)


def _addr_list(value: str) -> list[EmailAddress]:
    if not value:
        return []
    return [EmailAddress(email=e, name=n or None) for n, e in getaddresses([value]) if e]


def _norm_message(msg: dict, *, include_body: bool) -> EmailMessage:
    hdr = _headers_map(msg)
    snippet_src = first(msg, "preview", "snippet", "messageText")
    snippet = " ".join(snippet_src.split())[:200] if isinstance(snippet_src, str) else ""
    labels = msg.get("labelIds") or msg.get("labels") or []
    labels = list(labels) if isinstance(labels, list) else []
    em = EmailMessage(
        id=first(msg, "messageId", "message_id", "id") or None,
        thread_id=first(msg, "threadId", "thread_id") or None,
        sender=_addr(first(msg, "sender", "from") or hdr.get("from", "")),
        to=_addr_list(first(msg, "to", "recipient") or hdr.get("to", "")),
        subject=first(msg, "subject") or hdr.get("subject", "") or None,
        date=first(msg, "messageTimestamp", "date", "internalDate") or hdr.get("date", "") or None,
        snippet=snippet or None,
        labels=labels,
        unread="UNREAD" in labels,
    )
    if include_body:
        body = first(msg, "messageText", "body", "text", "textPlain")
        em.body = body[:MAX_BODY_CHARS] if isinstance(body, str) else None
    return em


def _messages_from(data: dict) -> list[dict]:
    for key in ("messages", "response_data", "data", "result"):
        value = data.get(key)
        if isinstance(value, list):
            return [m for m in value if isinstance(m, dict)]
    return []


# ─── tools ───────────────────────────────────────────────────────────────────


class GmailSearchArgs(BaseModel):
    query: str = Field(
        ..., description="Gmail search query, e.g. 'from:alice is:unread newer_than:7d'."
    )
    max_results: int = Field(10, description="Max messages to return (1-25).")


class GmailGetArgs(BaseModel):
    message_id: str = Field(..., description="Id of the message to read in full.")


class GmailDraftArgs(BaseModel):
    to: str = Field(..., description="Recipient email address.")
    subject: str
    body: str


class GmailSendArgs(BaseModel):
    to: str = Field(..., description="Recipient email address.")
    subject: str
    body: str


class GmailReplyArgs(BaseModel):
    message_id: str = Field(..., description="Id of the message you're replying to.")
    body: str


def _gmail_search(query: str, max_results: int = 10) -> ToolResult:
    n = max(1, min(int(max_results), MAX_LIST_RESULTS))
    data = _composio.execute("GMAIL_FETCH_EMAILS", {"query": query, "max_results": n})
    msgs = [_norm_message(m, include_body=False).model_dump() for m in _messages_from(data)]
    return ToolResult.ok(data={"count": len(msgs), "messages": msgs})


def _gmail_get(message_id: str) -> ToolResult:
    data = _composio.execute("GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", {"message_id": message_id})
    wrapped = _messages_from(data)
    msg = _norm_message(wrapped[0] if wrapped else data, include_body=True)
    return ToolResult.ok(data=msg.model_dump())


def _gmail_draft(to: str, subject: str, body: str) -> ToolResult:
    data = _composio.execute(
        "GMAIL_CREATE_EMAIL_DRAFT", {"recipient_email": to, "subject": subject, "body": body}
    )
    return ToolResult.ok(
        data={
            "draft_id": first(data, "id", "draftId", "draft_id") or None,
            "thread_id": first(data, "threadId", "thread_id") or None,
        },
        note="Draft saved.",
    )


def _gmail_send(to: str, subject: str, body: str, thread_id: str | None = None) -> ToolResult:
    args = {"recipient_email": to, "subject": subject, "body": body}
    if thread_id:
        args["thread_id"] = thread_id
    data = _composio.execute("GMAIL_SEND_EMAIL", args)
    return ToolResult.ok(
        data={
            "id": first(data, "id", "messageId", "message_id") or None,
            "thread_id": first(data, "threadId", "thread_id", default=thread_id) or None,
        },
        note=f"Sent to {to}.",
    )


def _gmail_reply(message_id: str, body: str) -> ToolResult:
    """Reply on a message's thread: derive recipient/subject/thread from the original."""
    data = _composio.execute("GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", {"message_id": message_id})
    wrapped = _messages_from(data)
    orig = _norm_message(wrapped[0] if wrapped else data, include_body=False)
    to = orig.sender.email if orig.sender else ""
    if not to:
        return ToolResult.error("Couldn't determine who to reply to on that message.")
    subject = orig.subject or ""
    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return _gmail_send(to=to, subject=subject, body=body, thread_id=orig.thread_id)


GMAIL_SEARCH_SPEC = ToolSpec.from_model(
    name="gmail_search",
    description="Search the user's Gmail and return matching message summaries.",
    args_model=GmailSearchArgs,
    handler=_gmail_search,
)
GMAIL_GET_SPEC = ToolSpec.from_model(
    name="gmail_get",
    description="Read one Gmail message in full (with body) by its id.",
    args_model=GmailGetArgs,
    handler=_gmail_get,
)
GMAIL_DRAFT_SPEC = ToolSpec.from_model(
    name="gmail_draft",
    description="Save a draft email (not sent). Use this to compose before sending.",
    args_model=GmailDraftArgs,
    handler=_gmail_draft,
)
GMAIL_SEND_SPEC = ToolSpec.from_model(
    name="gmail_send",
    description="Send a new email. Requires the user's confirmation.",
    args_model=GmailSendArgs,
    handler=_gmail_send,
    destructive=True,
)
GMAIL_REPLY_SPEC = ToolSpec.from_model(
    name="gmail_reply",
    description="Reply on the thread of an existing message. Requires the user's confirmation.",
    args_model=GmailReplyArgs,
    handler=_gmail_reply,
    destructive=True,
)

SPECS = [GMAIL_SEARCH_SPEC, GMAIL_GET_SPEC, GMAIL_DRAFT_SPEC, GMAIL_SEND_SPEC, GMAIL_REPLY_SPEC]
