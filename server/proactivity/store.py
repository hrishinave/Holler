"""Triggers store — SQLite persistence for scheduled tasks.

Same DB file as the conversation log (its own connection). A trigger fires when
``next_trigger`` (naive UTC ISO) is <= now and its status is 'active'; string
comparison is safe because every value is the same fixed-width UTC format.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from schemas import Trigger, TriggerStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    next_trigger    TEXT NOT NULL,
    repeat          TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_triggers_due ON triggers (status, next_trigger);

CREATE TABLE IF NOT EXISTS processed_emails (
    message_id TEXT PRIMARY KEY,
    notified   INTEGER NOT NULL DEFAULT 0,
    seen_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,
    conversation_id TEXT,
    content         TEXT,
    dedup_key       TEXT,
    delivered       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_outbox_dedup ON outbox (dedup_key);
"""

_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: tools run in worker threads (to_thread) while
        # the scheduler runs on the loop thread; both touch this store.
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
    return _conn


def set_connection(conn: sqlite3.Connection | None) -> None:
    """Inject a connection (e.g. in-memory) for tests."""
    global _conn
    _conn = conn
    if conn is not None:
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row(r: sqlite3.Row) -> Trigger:
    return Trigger(
        id=r["id"],
        conversation_id=r["conversation_id"],
        prompt=r["prompt"],
        next_trigger=r["next_trigger"],
        repeat=r["repeat"],
        status=TriggerStatus(r["status"]),
        created_at=r["created_at"],
    )


def create(conversation_id: str, prompt: str, next_trigger: str, repeat: str | None = None) -> Trigger:
    conn = _connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO triggers (conversation_id, prompt, next_trigger, repeat) VALUES (?, ?, ?, ?)",
            (conversation_id, prompt, next_trigger, repeat),
        )
    return get(cur.lastrowid)  # type: ignore[arg-type]


def get(trigger_id: int) -> Trigger | None:
    row = _connect().execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    return _row(row) if row else None


def due(now_iso: str) -> list[Trigger]:
    rows = _connect().execute(
        "SELECT * FROM triggers WHERE status = 'active' AND next_trigger <= ? ORDER BY next_trigger",
        (now_iso,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_for(conversation_id: str, *, active_only: bool = True) -> list[Trigger]:
    q = "SELECT * FROM triggers WHERE conversation_id = ?"
    if active_only:
        q += " AND status = 'active'"
    q += " ORDER BY next_trigger"
    rows = _connect().execute(q, (conversation_id,)).fetchall()
    return [_row(r) for r in rows]


def reschedule(trigger_id: int, next_trigger: str) -> None:
    conn = _connect()
    with conn:
        conn.execute("UPDATE triggers SET next_trigger = ? WHERE id = ?", (next_trigger, trigger_id))


def mark_done(trigger_id: int) -> None:
    conn = _connect()
    with conn:
        conn.execute("UPDATE triggers SET status = 'done' WHERE id = ?", (trigger_id,))


def cancel(trigger_id: int) -> bool:
    conn = _connect()
    with conn:
        cur = conn.execute(
            "UPDATE triggers SET status = 'cancelled' WHERE id = ? AND status = 'active'",
            (trigger_id,),
        )
    return cur.rowcount > 0


# --- processed emails (email monitor) ------------------------------------


def email_seen(message_id: str) -> bool:
    row = _connect().execute(
        "SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row is not None


def mark_email(message_id: str, notified: bool) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_emails (message_id, notified) VALUES (?, ?)",
            (message_id, 1 if notified else 0),
        )


# --- outbox (proactive delivery log + dedup) -----------------------------


def outbox_seen(dedup_key: str | None) -> bool:
    """True if an event with this key was already *delivered* (so retries of
    failed deliveries are still allowed)."""
    if not dedup_key:
        return False
    row = _connect().execute(
        "SELECT 1 FROM outbox WHERE dedup_key = ? AND delivered = 1 LIMIT 1", (dedup_key,)
    ).fetchone()
    return row is not None


def record_outbox(source: str, conversation_id: str, content: str,
                  dedup_key: str | None, delivered: bool) -> int:
    conn = _connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO outbox (source, conversation_id, content, dedup_key, delivered) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, conversation_id, content, dedup_key, 1 if delivered else 0),
        )
    return int(cur.lastrowid)


def outbox_recent(limit: int = 20) -> list[dict]:
    rows = _connect().execute(
        "SELECT id, source, conversation_id, content, delivered, created_at "
        "FROM outbox ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
