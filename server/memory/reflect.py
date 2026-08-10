"""Autonomous memory — the reflection pass.

The agent chats normally; separately, this reads the recent conversation and
distills any NEW durable facts about the user, storing them silently. That's how
it learns without being told "remember this": you grumble about an 8am standup,
and it quietly records that you dislike early meetings.

Debounced so it isn't a per-turn cost (and to respect model rate limits): it only
runs once enough new messages have accumulated, picks up where it left off via a
per-conversation marker, and is shown existing facts so it doesn't duplicate.

Conservative by design — only durable, general preferences/relationships/habits,
never one-off task details.
"""

from __future__ import annotations

from agent.prompts import load_prompt
from llm import chat
from memory import facts
from memory import store as mstore

# Min new messages before a reflection pass (~2 turns of user+assistant+tool).
REFLECT_AFTER = 8

_INSTRUCTION = (
    "You maintain a long-term memory of durable facts about the user. Given the "
    "recent conversation and the facts you already know, list any NEW durable "
    "facts worth remembering across future chats — preferences, people who matter, "
    "habits, constraints, recurring context. Be conservative: skip one-off task "
    "details, anything already known, and anything you're unsure is durable. "
    "Output ONE short fact per line (no numbering), or exactly NONE if there's "
    "nothing new."
)


def _transcript(items: list[tuple[int, dict]]) -> str:
    lines = []
    for _id, msg in items:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content.strip()}")
    return "\n".join(lines)


def _parse_facts(reply: str) -> list[str]:
    out = []
    for line in (reply or "").splitlines():
        cleaned = line.strip().lstrip("-•*0123456789. ").strip()
        if not cleaned or cleaned.upper() == "NONE":
            continue
        out.append(cleaned)
    return out


async def reflect(conversation_id: str) -> list[str]:
    """Run one reflection pass. Returns the newly-learned facts (may be empty)."""
    last = mstore.last_reflected(conversation_id)
    items = mstore.messages_since(conversation_id, last)
    if not items:
        return []

    transcript = _transcript(items)
    if not transcript.strip():
        mstore.set_reflected(conversation_id, items[-1][0])
        return []

    known = [f["content"] for f in facts.list_facts()]
    prompt = (
        "Facts you already know about the user:\n"
        + ("\n".join(f"- {k}" for k in known) if known else "(none yet)")
        + "\n\nRecent conversation:\n"
        + transcript
    )
    system = load_prompt("voice") + "\n\n" + _INSTRUCTION
    resp = await chat([{"role": "user", "content": prompt}], system=system)
    reply = resp["choices"][0]["message"].get("content") or ""

    new_facts = _parse_facts(reply)
    known_lower = {k.lower() for k in known}
    stored = []
    for fact in new_facts:
        if fact.lower() in known_lower:
            continue  # belt-and-suspenders dedup
        facts.add_fact(fact)
        stored.append(fact)

    mstore.set_reflected(conversation_id, items[-1][0])
    return stored


async def maybe_reflect(conversation_id: str) -> list[str]:
    """Reflect only if enough new messages have accumulated (debounce)."""
    last = mstore.last_reflected(conversation_id)
    pending = mstore.messages_since(conversation_id, last)
    if len(pending) < REFLECT_AFTER:
        return []
    return await reflect(conversation_id)
