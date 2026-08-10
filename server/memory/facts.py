"""Long-term semantic memory: durable facts/preferences about the user, plus
deterministic email rules.

Two kinds of knowledge:

* ``facts`` — free-text statements ("My manager is Priya", "I hate 8am meetings").
  These are injected into the system prompt (and email triage) as soft context,
  so the model applies them with judgment, everywhere.
* ``email_prefs`` — hard skip/flag rules matched by substring against a message's
  sender+subject. Deterministic: guaranteed behavior for clear-cut cases like
  "never flag security alerts", no model involved.

Single-user assistant, so facts are global (not per-conversation).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS email_prefs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,          -- 'skip' | 'flag'
    pattern    TEXT NOT NULL,          -- substring matched vs sender+subject
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
    return _conn


def set_connection(conn: sqlite3.Connection | None) -> None:
    global _conn
    _conn = conn
    if conn is not None:
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)


# --- facts ---------------------------------------------------------------


def add_fact(content: str) -> int:
    conn = _connect()
    with conn:
        cur = conn.execute("INSERT INTO facts (content) VALUES (?)", (content.strip(),))
    return int(cur.lastrowid)


def list_facts() -> list[dict]:
    rows = _connect().execute("SELECT id, content FROM facts ORDER BY id").fetchall()
    return [{"id": r["id"], "content": r["content"]} for r in rows]


def delete_fact(fact_id: int) -> bool:
    conn = _connect()
    with conn:
        cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    return cur.rowcount > 0


# --- email rules ---------------------------------------------------------


def add_email_pref(kind: str, pattern: str) -> int:
    conn = _connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO email_prefs (kind, pattern) VALUES (?, ?)", (kind, pattern.strip())
        )
    return int(cur.lastrowid)


def list_email_prefs() -> list[dict]:
    rows = _connect().execute("SELECT id, kind, pattern FROM email_prefs ORDER BY id").fetchall()
    return [{"id": r["id"], "kind": r["kind"], "pattern": r["pattern"]} for r in rows]


def delete_email_pref(pref_id: int) -> bool:
    conn = _connect()
    with conn:
        cur = conn.execute("DELETE FROM email_prefs WHERE id = ?", (pref_id,))
    return cur.rowcount > 0


def match_email_pref(sender: str, subject: str) -> str | None:
    """Return 'skip'/'flag'/None for a message. Skip wins over flag (deny-first)."""
    text = f"{sender} {subject}".lower()
    prefs = list_email_prefs()
    if any(p["kind"] == "skip" and p["pattern"].lower() in text for p in prefs):
        return "skip"
    if any(p["kind"] == "flag" and p["pattern"].lower() in text for p in prefs):
        return "flag"
    return None


# --- prompt injection ----------------------------------------------------


def facts_block() -> str:
    """A 'what you know about the user' block for the system prompt. Empty if none."""
    facts = list_facts()
    prefs = list_email_prefs()
    if not facts and not prefs:
        return ""
    lines = ["## What you know about the user"]
    for f in facts:
        lines.append(f"- {f['content']}")
    skips = [p["pattern"] for p in prefs if p["kind"] == "skip"]
    flags = [p["pattern"] for p in prefs if p["kind"] == "flag"]
    if skips:
        lines.append(f"- For email, never flag messages matching: {', '.join(skips)}")
    if flags:
        lines.append(f"- For email, always flag messages matching: {', '.join(flags)}")
    return "\n".join(lines)
