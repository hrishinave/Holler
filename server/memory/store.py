"""Conversation log — SQLite persistence for the transcript.

The loop (`run_turn`) stays pure: it takes a history list and returns the updated
one. This module is what a channel (REPL now, Telegram later) uses to *remember*
that history across restarts. One row per message, stored as the full message
dict (JSON) so a reload reconstructs a valid OpenAI-format transcript — including
assistant ``tool_calls`` and ``tool`` results.

Storage is keyed by ``conversation_id`` (e.g. "repl", or a Telegram chat id), so
multiple conversations coexist in one file.

sqlite3 is synchronous; calls are local and fast. Under the async server, wrap
these in ``asyncio.to_thread`` (same pattern the tool layer uses).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT,
    message_json    TEXT NOT NULL,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS reflection_state (
    conversation_id TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS summaries (
    conversation_id TEXT PRIMARY KEY,
    covered_count   INTEGER NOT NULL,   -- how many leading messages the summary covers
    summary         TEXT NOT NULL
);
"""

_conn: sqlite3.Connection | None = None


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # WAL lets background reflection write while a turn reads; busy_timeout waits
    # out a brief writer instead of raising SQLITE_BUSY.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.OperationalError:
        pass


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False for consistency with the async server (tools/
        # scheduler may touch stores from worker threads).
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _apply_pragmas(_conn)
        _conn.executescript(_SCHEMA)
    return _conn


def set_connection(conn: sqlite3.Connection | None) -> None:
    """Inject a connection (e.g. ``sqlite3.connect(':memory:')``) for tests."""
    global _conn
    _conn = conn
    if conn is not None:
        _apply_pragmas(conn)
        conn.executescript(_SCHEMA)


def append_messages(conversation_id: str, messages: list[dict]) -> None:
    """Append the new messages from a turn (the delta) to the log."""
    if not messages:
        return
    conn = _connect()
    with conn:
        conn.executemany(
            "INSERT INTO messages (conversation_id, role, message_json) VALUES (?, ?, ?)",
            [
                (conversation_id, m.get("role"), json.dumps(m, default=str))
                for m in messages
            ],
        )


def load_history(conversation_id: str) -> list[dict]:
    """Return the full transcript for a conversation, in order.

    Full (not truncated) so tool-call/tool-result pairs stay intact — compaction
    is ``summarize.compact``'s job, not a blind tail slice.
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT message_json FROM messages WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def clear(conversation_id: str) -> None:
    """Forget a conversation."""
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))


def count(conversation_id: str) -> int:
    conn = _connect()
    return conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()[0]


# --- reflection support (autonomous memory) ------------------------------


def messages_since(conversation_id: str, after_id: int = 0) -> list[tuple[int, dict]]:
    """(row_id, message) pairs newer than ``after_id``, in order."""
    rows = _connect().execute(
        "SELECT id, message_json FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id",
        (conversation_id, after_id),
    ).fetchall()
    return [(r[0], json.loads(r[1])) for r in rows]


def last_reflected(conversation_id: str) -> int:
    row = _connect().execute(
        "SELECT last_message_id FROM reflection_state WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return row[0] if row else 0


def set_reflected(conversation_id: str, last_message_id: int) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO reflection_state (conversation_id, last_message_id) VALUES (?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET last_message_id = excluded.last_message_id",
            (conversation_id, last_message_id),
        )


# --- compaction checkpoints ----------------------------------------------


def get_summary(conversation_id: str) -> tuple[int, str] | None:
    row = _connect().execute(
        "SELECT covered_count, summary FROM summaries WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def set_summary(conversation_id: str, covered_count: int, summary: str) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO summaries (conversation_id, covered_count, summary) VALUES (?, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "covered_count = excluded.covered_count, summary = excluded.summary",
            (conversation_id, covered_count, summary),
        )


def clear_summary(conversation_id: str) -> None:
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM summaries WHERE conversation_id = ?", (conversation_id,))
