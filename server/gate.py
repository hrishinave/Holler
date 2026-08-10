"""Destructive-action approval gate — the pending-action model.

The rule (ported from the S16 planner gate): a destructive action — deleting an
event, sending or replying to mail, inviting people to an event — must not run
unless the user approved *that exact action* in their own message. Enforced in
the loop (code), not left to the prompt.

How it works (variant B — execute the stored args):

1. When the model calls a destructive tool, the loop doesn't run it. It calls
   ``propose`` to freeze the tool + validated args + a human preview into the
   ``pending_actions`` table, and tells the model to show the preview and ask.
2. On a later turn, if the user's message ``reads_as_approval`` and a valid
   pending action exists, the loop executes the *stored* arguments verbatim and
   ``consume``s the record. The model never regenerates the args after review, so
   what the user approved is exactly what runs.

Binding to one stored action (not a per-turn boolean) is what fixes the old
regex gate: a stray "send it" inside "please do not send it" is vetoed by the
negation check; one "yes" can only approve the single latest proposal; changed
args can't ride an old approval; and a consumed/expired record can't fire twice.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import settings
from schemas import PendingAction, PendingStatus

# How long a proposed action stays approvable. Short: an approval should follow
# the proposal promptly, not resurface an hour later out of context.
TTL_MINUTES = 15

# Approval must be an actual go-ahead word (word-boundary, so "yesterday" isn't
# "yes"), AND the message must not also negate. The negation veto is the fix for
# "please do not send it" — which contains "send it" but plainly refuses.
_AFFIRM_RE = re.compile(
    r"\b(yes|yep|yeah|yup|sure|confirm|confirmed|approved?|go ahead|do it|"
    r"send it|send them|go for it|proceed|please do|sounds good|ok(?:ay)? send)\b",
    re.IGNORECASE,
)
_NEGATE_RE = re.compile(
    r"\b(no|nope|don'?t|do not|never|stop|wait|cancel|hold on|nvm|"
    r"nevermind|never mind|scratch that)\b",
    re.IGNORECASE,
)


def reads_as_approval(user_text: str) -> bool:
    """True if the message is an affirmative go-ahead and not a refusal."""
    text = user_text or ""
    return bool(_AFFIRM_RE.search(text)) and not _NEGATE_RE.search(text)


# Back-compat alias: some phase scripts still import ``is_authorized``. Same
# negation-aware logic now.
is_authorized = reads_as_approval


# --- preview rendering ----------------------------------------------------


def render_preview(tool: str, args: dict) -> str:
    """A human, args-grounded description of what will run if approved.

    Built from the *arguments*, not the model's prose, so the user reviews the
    thing that will actually execute.
    """
    if tool == "gmail_send":
        return (
            f"Send an email to {args.get('to', '?')} — subject "
            f"{args.get('subject', '') or '(no subject)'!r}:\n\n{args.get('body', '')}"
        )
    if tool == "gmail_reply":
        return f"Reply on that thread:\n\n{args.get('body', '')}"
    if tool == "calendar_delete":
        return f"Delete calendar event {args.get('event_id') or args.get('id') or '?'}."
    if tool == "calendar_create":
        who = ", ".join(args.get("attendees") or [])
        summary = args.get("summary", "event")
        return f"Create '{summary}' and invite {who}." if who else f"Create '{summary}'."
    return f"Run {tool} with {json.dumps(args, default=str)}"


# --- pending-action store -------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool            TEXT NOT NULL,
    args_json       TEXT NOT NULL,
    preview         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_conv ON pending_actions (conversation_id, status);
"""

_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.executescript(_SCHEMA)
    return _conn


def set_connection(conn: sqlite3.Connection | None) -> None:
    """Inject a connection (e.g. ``sqlite3.connect(':memory:')``) for tests."""
    global _conn
    _conn = conn
    if conn is not None:
        conn.executescript(_SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _to_model(row: tuple, status: PendingStatus | None = None) -> PendingAction:
    return PendingAction(
        id=row[0],
        conversation_id=row[1],
        tool=row[2],
        arguments=json.loads(row[3]),
        preview=row[4] or "",
        status=status or PendingStatus(row[5]),
        created_at=row[6],
        expires_at=row[7],
    )


_COLS = "id, conversation_id, tool, args_json, preview, status, created_at, expires_at"


def propose(conversation_id: str, tool: str, arguments: dict, preview: str | None = None) -> PendingAction:
    """Record a destructive action awaiting approval.

    Supersedes any earlier still-pending action in this conversation, so there is
    at most one live proposal to approve — one "yes" is unambiguous.
    """
    conn = _connect()
    now = _now()
    expires = now + timedelta(minutes=TTL_MINUTES)
    pid = secrets.token_hex(8)
    prev = preview if preview is not None else render_preview(tool, arguments)
    with conn:
        conn.execute(
            "UPDATE pending_actions SET status='superseded' "
            "WHERE conversation_id=? AND status='pending'",
            (conversation_id,),
        )
        conn.execute(
            f"INSERT INTO pending_actions ({_COLS}) VALUES (?,?,?,?,?,?,?,?)",
            (pid, conversation_id, tool, json.dumps(arguments, default=str),
             prev, "pending", _iso(now), _iso(expires)),
        )
    return PendingAction(
        id=pid, conversation_id=conversation_id, tool=tool, arguments=arguments,
        preview=prev, status=PendingStatus.PENDING, created_at=_iso(now), expires_at=_iso(expires),
    )


def take_approved(conversation_id: str) -> PendingAction | None:
    """Consume and return the latest valid pending action, or None.

    Expires any stale (past-TTL) pending records first, then claims the newest
    remaining one — marking it ``consumed`` so it can never fire twice.
    """
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE pending_actions SET status='expired' "
            "WHERE conversation_id=? AND status='pending' AND expires_at < ?",
            (conversation_id, _iso(_now())),
        )
        row = conn.execute(
            f"SELECT {_COLS} FROM pending_actions "
            "WHERE conversation_id=? AND status='pending' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE pending_actions SET status='consumed' WHERE id=?", (row[0],))
    return _to_model(row, status=PendingStatus.CONSUMED)


def latest_pending(conversation_id: str) -> PendingAction | None:
    """Peek at the current live proposal without consuming it (tests/inspection)."""
    row = _connect().execute(
        f"SELECT {_COLS} FROM pending_actions "
        "WHERE conversation_id=? AND status='pending' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return _to_model(row) if row else None


def clear(conversation_id: str) -> None:
    """Drop all pending actions for a conversation (e.g. on /reset)."""
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM pending_actions WHERE conversation_id=?", (conversation_id,))
