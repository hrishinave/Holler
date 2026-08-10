"""History compaction — rolling, non-destructive.

Loading the full transcript every turn grows cost without bound (O(n^2) over a
conversation) and eventually blows the context window. This bounds the
*model-facing* view: older turns are folded into a cached summary note, recent
turns are kept verbatim. The raw messages stay in the DB untouched — this only
shapes what we send to the model.

Two invariants:
- **Never split a tool exchange.** The kept tail always starts on a ``user``
  message (a turn boundary), so no ``tool`` result is ever orphaned from its
  assistant ``tool_calls``.
- **Don't re-summarize every turn.** A summary checkpoint (persisted) records how
  many leading messages it covers; we only roll it forward once the verbatim tail
  grows past a margin. Most turns are a cheap cache read, no model call.
"""

from __future__ import annotations

from llm import chat
from memory import store as mstore

# Below this many messages, don't compact at all.
COMPACT_THRESHOLD = 30
# Keep roughly this many recent messages verbatim.
KEEP_RECENT = 14
# Re-summarize (roll the checkpoint forward) once the tail exceeds KEEP_RECENT by this.
RESUMMARIZE_EVERY = 12
# Don't bother summarizing fewer than this many old messages.
MIN_OLD = 6

_SUMMARY_SYSTEM = (
    "You maintain a compact running summary of a conversation between a user and "
    "their assistant. Fold the new messages into the existing summary. Capture "
    "durable context, decisions, facts about the user, and open threads. Drop "
    "small talk. Keep it tight — a few short bullets or sentences."
)


def _clean_cut(messages: list[dict], target: int) -> int:
    """Move ``target`` forward to the next ``user`` message (a turn boundary)."""
    cut = max(0, target)
    while cut < len(messages) and messages[cut].get("role") != "user":
        cut += 1
    return cut


def _transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content.strip()}")
        elif role == "assistant" and m.get("tool_calls"):
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in m["tool_calls"])
            lines.append(f"assistant: [used tools: {names}]")
    return "\n".join(lines)


async def _summarize(prev_summary: str | None, messages: list[dict]) -> str:
    prefix = f"Existing summary:\n{prev_summary}\n\n" if prev_summary else ""
    user = prefix + "New messages to fold in:\n" + _transcript(messages)
    resp = await chat([{"role": "user", "content": user}], system=_SUMMARY_SYSTEM)
    return (resp["choices"][0]["message"].get("content") or "").strip()


def _with_summary(summary: str, recent: list[dict]) -> list[dict]:
    note = {"role": "system", "content": f"Summary of earlier conversation:\n{summary}"}
    return [note] + recent


async def compact(conversation_id: str, messages: list[dict]) -> list[dict]:
    """Return the model-facing view of ``messages`` (summary note + recent tail).

    Falls back to the full list on any summarization failure — better to send too
    much than to crash a turn.
    """
    n = len(messages)
    cached = mstore.get_summary(conversation_id)

    # Stale checkpoint (history was cleared/reset): drop it and start over.
    if cached is not None and cached[0] > n:
        mstore.clear_summary(conversation_id)
        cached = None

    try:
        if cached is None:
            if n <= COMPACT_THRESHOLD:
                return messages
            cut = _clean_cut(messages, n - KEEP_RECENT)
            if cut < MIN_OLD or cut >= n:
                return messages
            summary = await _summarize(None, messages[:cut])
            if not summary:
                return messages
            mstore.set_summary(conversation_id, cut, summary)
            return _with_summary(summary, messages[cut:])

        covered, summary = cached
        if n - covered <= KEEP_RECENT + RESUMMARIZE_EVERY:
            # Tail still small enough — reuse the cached summary, no model call.
            return _with_summary(summary, messages[covered:])

        new_cut = _clean_cut(messages, n - KEEP_RECENT)
        if new_cut <= covered:
            return _with_summary(summary, messages[covered:])
        summary = await _summarize(summary, messages[covered:new_cut])
        if not summary:
            return _with_summary(cached[1], messages[covered:])
        mstore.set_summary(conversation_id, new_cut, summary)
        return _with_summary(summary, messages[new_cut:])
    except Exception as exc:  # rate limit, etc. — never break the turn
        print("compaction skipped:", exc, flush=True)
        if cached is not None:
            return _with_summary(cached[1], messages[cached[0]:])
        return messages
