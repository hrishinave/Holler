"""Autonomous memory — the reflection pass.

The agent chats normally; separately, this reads the recent conversation and
proposes durable updates to the user model — typed beliefs, not flat sentences.
It distinguishes what the user *told* it from what it *guessed*, holds guesses as
weak hypotheses, supersedes corrected beliefs instead of hoarding both, and
refuses to learn sensitive personal traits or secrets.

Debounced (per-conversation marker, min new user turns) so it isn't a per-turn
cost and respects model rate limits. Uses a dedicated extraction prompt — not the
conversational voice — and never logs learned content in plaintext.
"""

from __future__ import annotations

import json
import re

from agent.prompts import load_prompt
from llm import chat
from memory import facts
from memory import store as mstore
from schemas import MemoryProposal, MemorySource, MemoryStrength

# Reflect once this many NEW user turns have accrued since the last pass.
REFLECT_AFTER_USER_TURNS = 3

# Defense-in-depth beyond the prompt: drop any proposal whose content looks like a
# secret or a sensitive personal trait, even if the model proposes it anyway.
_PROHIBITED = re.compile(
    r"\b(?:"
    # secrets / credentials
    r"password|passcode|api[\s_-]?key|secret\s?key|access\s?token|auth\s?code|"
    r"verification\s?code|one[\s-]?time\s?code|otp|ssn|social security|"
    r"credit\s?card|card\s?number|account\s?number|routing\s?number|"
    # health / mental health (stems allow suffixes: anxiety, depressed, diagnosis)
    r"anxiet\w*|depress\w*|bipolar|adhd|schizo\w*|ptsd|diagnos\w*|therap\w*|"
    r"medicat\w*|pregnan\w*|hiv|std|"
    # politics / religion / orientation
    r"republican|democrat\w*|conservativ\w*|liberal|socialist|"
    r"christian|muslim|jewish|hindu|buddhist|atheist|catholic|"
    r"gay|lesbian|bisexual|transgender|sexual orientation|"
    # finances
    r"salary|salaries|net worth|bankrupt\w*|in debt|income"
    r")\b",
    re.IGNORECASE,
)


def _is_sensitive(content: str) -> bool:
    return bool(_PROHIBITED.search(content or ""))


def _user_turns(items: list[tuple[int, dict]]) -> int:
    return sum(1 for _id, m in items if m.get("role") == "user")


def _transcript(items: list[tuple[int, dict]]) -> str:
    lines = []
    for _id, msg in items:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content.strip()}")
    return "\n".join(lines)


def _known_block() -> str:
    mems = facts.list_memories()
    if not mems:
        return "(none yet)"
    return "\n".join(
        f"- key={m['canonical_key'] or '—'} [{m['strength']}/{m['source']}] {m['content']}"
        for m in mems
    )


def _parse_proposals(reply: str) -> list[MemoryProposal]:
    raw = (reply or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        obj = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for item in obj.get("memories", []) if isinstance(obj, dict) else []:
        try:
            out.append(MemoryProposal.model_validate(item))
        except Exception:
            continue  # a malformed proposal is dropped, not fatal
    return out


async def reflect(conversation_id: str) -> list[dict]:
    """Run one reflection pass. Returns the beliefs actually stored (structured)."""
    last = mstore.last_reflected(conversation_id)
    items = mstore.messages_since(conversation_id, last)
    if not items:
        return []

    transcript = _transcript(items)
    if not transcript.strip():
        mstore.set_reflected(conversation_id, items[-1][0])
        return []

    prompt = (
        "Things already known about the user:\n" + _known_block()
        + "\n\nRecent conversation:\n" + transcript
    )
    resp = await chat(
        [{"role": "user", "content": prompt}],
        system=load_prompt("memory_reflection"),
    )
    reply = resp["choices"][0]["message"].get("content") or ""

    stored: list[dict] = []
    for p in _parse_proposals(reply):
        if not p.is_store():
            continue
        if _is_sensitive(p.content):
            # Refused by the sensitive-data boundary. Log the refusal, not the content.
            print(f"[reflect] refused a sensitive proposal (kind={p.kind.value})", flush=True)
            continue
        result = facts.add_memory(
            p.content, kind=p.kind, source=p.source, strength=p.strength,
            canonical_key=p.canonical_key, expires_at=p.expires_at,
        )
        if result.get("stored"):
            stored.append({
                "id": result["id"], "kind": p.kind.value, "source": p.source.value,
                "strength": p.strength.value, "canonical_key": p.canonical_key,
                "content": p.content,
            })

    mstore.set_reflected(conversation_id, items[-1][0])
    return stored


async def maybe_reflect(conversation_id: str) -> list[dict]:
    """Reflect only once enough new user turns have accrued (debounce)."""
    last = mstore.last_reflected(conversation_id)
    pending = mstore.messages_since(conversation_id, last)
    if _user_turns(pending) < REFLECT_AFTER_USER_TURNS:
        return []
    return await reflect(conversation_id)


__all__ = ["reflect", "maybe_reflect", "REFLECT_AFTER_USER_TURNS"]
