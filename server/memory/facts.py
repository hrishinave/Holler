"""The semantic user model: a small store of typed beliefs about the user, plus
deterministic email rules.

Each belief (``memory_items`` row) carries *kind*, *source* (told vs. corrected
vs. inferred), *strength* (hard constraint / preference / hypothesis), an optional
*canonical key* so the same concept occupies one slot, and an optional expiry.
Supersession is source-aware: what the user tells or corrects outranks what the
agent merely inferred, so a guess can never overwrite a stated fact.

``email_prefs`` are unchanged: hard skip/flag substring rules for the inbox.

Single-user assistant, so beliefs are global (not per-conversation).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from schemas import MemoryItem, MemoryKind, MemorySource, MemoryStatus, MemoryStrength, SOURCE_AUTHORITY

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key  TEXT,
    kind           TEXT NOT NULL,
    content        TEXT NOT NULL,
    source         TEXT NOT NULL,
    strength       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    supersedes_id  INTEGER,
    expires_at     TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_items (canonical_key, status);

CREATE TABLE IF NOT EXISTS email_prefs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,          -- 'skip' | 'flag'
    pattern    TEXT NOT NULL,          -- substring matched vs sender+subject
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Legacy flat facts (id, content) predate the typed model. Migrate them once, as
# low-authority inferred preferences, so nothing is lost and nothing buggy from
# the old free-text era is treated as a hard truth.
_LEGACY = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, created_at TEXT
);
"""

_conn: sqlite3.Connection | None = None


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # WAL lets background reflection write while a turn reads without locking;
    # busy_timeout waits out a brief writer instead of raising immediately.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.OperationalError:
        pass  # e.g. :memory: may reject WAL; correctness doesn't depend on it


def _init(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    conn.executescript(_SCHEMA)
    _migrate_legacy_facts(conn)


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _init(_conn)
    return _conn


def set_connection(conn: sqlite3.Connection | None) -> None:
    global _conn
    _conn = conn
    if conn is not None:
        _init(conn)


def _migrate_legacy_facts(conn: sqlite3.Connection) -> None:
    """Copy any old free-text facts into the typed store, once."""
    has_legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
    ).fetchone()
    if not has_legacy:
        return
    rows = conn.execute("SELECT content FROM facts ORDER BY id").fetchall()
    if not rows:
        return
    already = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
    if already:
        return  # a prior migration already ran
    with conn:
        for r in rows:
            conn.execute(
                "INSERT INTO memory_items (canonical_key, kind, content, source, strength) "
                "VALUES (NULL, ?, ?, ?, ?)",
                (MemoryKind.FACT.value, r["content"], MemorySource.INFERRED.value,
                 MemoryStrength.PREFERENCE.value),
            )
        conn.execute("DROP TABLE facts")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_item(r: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=r["id"], canonical_key=r["canonical_key"], kind=MemoryKind(r["kind"]),
        content=r["content"], source=MemorySource(r["source"]),
        strength=MemoryStrength(r["strength"]), status=MemoryStatus(r["status"]),
        supersedes_id=r["supersedes_id"], expires_at=r["expires_at"],
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


# --- memory items --------------------------------------------------------


def add_memory(
    content: str,
    *,
    kind: MemoryKind | str = MemoryKind.FACT,
    source: MemorySource | str = MemorySource.EXPLICIT,
    strength: MemoryStrength | str = MemoryStrength.PREFERENCE,
    canonical_key: str | None = None,
    expires_at: str | None = None,
) -> dict:
    """Store a belief. If it shares a canonical key with an active belief, the
    higher-authority source supersedes the lower — and a mere inference can never
    overwrite something the user told us. Returns {id, stored, superseded_id}."""
    # MemoryKind(x) accepts either an enum member or its string value.
    kind = MemoryKind(kind).value
    source = MemorySource(source).value
    strength = MemoryStrength(strength).value
    content = (content or "").strip()
    if not content:
        return {"id": None, "stored": False, "reason": "empty"}

    conn = _connect()
    existing = None
    if canonical_key:
        existing = conn.execute(
            "SELECT * FROM memory_items WHERE canonical_key=? AND status='active' "
            "ORDER BY id DESC LIMIT 1",
            (canonical_key,),
        ).fetchone()

    if existing is not None:
        if SOURCE_AUTHORITY[source] < SOURCE_AUTHORITY[existing["source"]]:
            # A weaker source (an inference) must not override a stated fact.
            return {"id": existing["id"], "stored": False, "reason": "outranked"}
        if existing["content"].strip().lower() == content.lower():
            return {"id": existing["id"], "stored": False, "reason": "duplicate"}

    with conn:
        if existing is not None:
            conn.execute(
                "UPDATE memory_items SET status='superseded', updated_at=? WHERE id=?",
                (_now(), existing["id"]),
            )
        cur = conn.execute(
            "INSERT INTO memory_items "
            "(canonical_key, kind, content, source, strength, status, supersedes_id, expires_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?, 'active', ?, ?, ?, ?)",
            (canonical_key, kind, content, source, strength,
             existing["id"] if existing is not None else None, expires_at, _now(), _now()),
        )
    return {"id": int(cur.lastrowid), "stored": True,
            "superseded_id": existing["id"] if existing is not None else None}


def list_memories(*, include_superseded: bool = False, include_expired: bool = False) -> list[dict]:
    """Active, non-expired beliefs (newest first), as plain dicts."""
    conn = _connect()
    where = "" if include_superseded else "WHERE status='active'"
    rows = conn.execute(f"SELECT * FROM memory_items {where} ORDER BY id DESC").fetchall()
    now = _now()
    out = []
    for r in rows:
        if not include_expired and r["expires_at"] and r["expires_at"] < now:
            continue
        out.append(_row_to_item(r).model_dump())
    return out


def get_memory(memory_id: int) -> dict | None:
    r = _connect().execute("SELECT * FROM memory_items WHERE id=?", (int(memory_id),)).fetchone()
    return _row_to_item(r).model_dump() if r else None


def delete_memory(memory_id: int) -> bool:
    """Hard-delete one belief (real removal, not hide)."""
    conn = _connect()
    with conn:
        cur = conn.execute("DELETE FROM memory_items WHERE id=?", (int(memory_id),))
    return cur.rowcount > 0


def clear_memories() -> int:
    """Hard-delete every belief (for an explicit, confirmed forget-all)."""
    conn = _connect()
    with conn:
        cur = conn.execute("DELETE FROM memory_items")
    return cur.rowcount


# --- email rules (unchanged) ---------------------------------------------


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

_SOURCE_LABEL = {
    "explicit": "you were told",
    "corrected": "you corrected this",
    "inferred": "inferred, unconfirmed",
}


def memory_block() -> str:
    """A 'what you know about the user' block for the system prompt.

    Labels each belief with its strength and source, and states how to weight
    them — so a guess informs but never dictates. Empty if there's nothing.
    """
    mems = list_memories()
    prefs = list_email_prefs()
    if not mems and not prefs:
        return ""
    lines = [
        "## What you know about the user",
        "Background context, not instructions. Weight by strength: a hard "
        "constraint is a rule to obey; a preference is a default to favor when "
        "practical; a hypothesis is an unconfirmed guess — don't act on it, at "
        "most let it break a tie. The user's current message always overrides.",
    ]
    for m in mems:
        label = _SOURCE_LABEL.get(m["source"], m["source"])
        lines.append(f"- ({m['strength']} · {label}) {m['content']}")
    skips = [p["pattern"] for p in prefs if p["kind"] == "skip"]
    flags = [p["pattern"] for p in prefs if p["kind"] == "flag"]
    if skips:
        lines.append(f"- For email, never flag messages matching: {', '.join(skips)}")
    if flags:
        lines.append(f"- For email, always flag messages matching: {', '.join(flags)}")
    return "\n".join(lines)


# --- backward-compatible shims (older callers/tests) ---------------------


def add_fact(content: str) -> int:
    """Deprecated: store a plain explicit fact. Prefer add_memory."""
    return add_memory(content, source=MemorySource.EXPLICIT).get("id") or 0


def list_facts() -> list[dict]:
    """Deprecated: beliefs as {id, content}. Prefer list_memories."""
    return [{"id": m["id"], "content": m["content"]} for m in list_memories()]


def delete_fact(fact_id: int) -> bool:
    """Deprecated: prefer delete_memory."""
    return delete_memory(fact_id)


def facts_block() -> str:
    """Deprecated alias for memory_block."""
    return memory_block()
