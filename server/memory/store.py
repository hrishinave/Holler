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
"""

_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False for consistency with the async server (tools/
        # scheduler may touch stores from worker threads).
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.executescript(_SCHEMA)
    return _conn


def set_connection(conn: sqlite3.Connection | None) -> None:
    """Inject a connection (e.g. ``sqlite3.connect(':memory:')``) for tests."""
    global _conn
    _conn = conn
    if conn is not None:
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
