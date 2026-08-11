"""Telegram channel: parse incoming updates, run the loop, send the reply.

This is the second entrypoint next to the REPL — same engine (`run_turn`), same
memory, same gate; only the transport differs. An incoming Telegram message is
handled exactly like a typed REPL line: authorization is read from the message
text, history is keyed per chat, and the reply is sent back.

Ported from autoagent's adapter: the valuable ``_to_telegram_text`` (GitHub
markdown -> clean chat text, sent with NO parse_mode so Telegram can't reject a
mis-escaped message). The S16 pairing/trust/attachment machinery is dropped for a
lean single-owner bot, replaced by a simple chat-id allowlist.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from agent.loop import run_turn
from config import settings
from context import set_conversation
from memory import store
from memory.reflect import maybe_reflect
from memory.summarize import compact

_API = "https://api.telegram.org/bot{token}/{method}"


def _to_telegram_text(text: str) -> str:
    """Render the agent's GitHub-flavored markdown as clean chat text.

    We send with no parse_mode, so raw ``**`` / ``##`` would show as noise;
    strip them to plain, human-looking prose. Plain text never fails to deliver,
    unlike MarkdownV2/HTML where one unescaped char rejects the whole message.
    """
    if not text:
        return ""
    text = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", text)  # drop code fences, keep code
    out_lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", raw)        # ## Header -> Header
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)      # - item   -> • item
        out_lines.append(line)
    text = "\n".join(out_lines)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)             # **bold**
    text = re.sub(r"__(.+?)__", r"\1", text)                 # __bold__
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)  # *italic*
    text = re.sub(r"`([^`]+)`", r"\1", text)                 # `code`
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)  # [t](url)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)     # [t](anchor)
    text = re.sub(r"\n{3,}", "\n\n", text)                   # collapse blank runs
    return text.strip()


def _allowed(chat_id: str) -> bool:
    """Owner allowlist. Empty setting = open (documented as unsafe)."""
    raw = settings.TELEGRAM_ALLOWED_CHAT_IDS.strip()
    if not raw:
        return True
    allowed = {c.strip() for c in raw.split(",") if c.strip()}
    return str(chat_id) in allowed


def extract(update: dict) -> tuple[str, str] | None:
    """Pull (chat_id, text) out of a Telegram update, or None if not a text message."""
    msg = update.get("message") or update.get("edited_message")
    if not isinstance(msg, dict):
        return None
    text = msg.get("text") or msg.get("caption")
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not text or chat_id is None:
        return None
    return str(chat_id), text


async def send(chat_id: str, text: str) -> dict:
    """Send a message, cleaning markdown first. No token => return the payload (dry run)."""
    payload = {
        "chat_id": int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id,
        "text": _to_telegram_text(text),
    }
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return payload
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _API.format(token=token, method="sendMessage"), json=payload, timeout=15.0
        )
        return resp.json()


async def handle_update(update: dict) -> None:
    """The controller: one incoming update -> a turn -> a reply. Used by both the
    webhook and the long-poller."""
    parsed = extract(update)
    if parsed is None:
        return
    chat_id, text = parsed

    if not _allowed(chat_id):
        await send(chat_id, "Sorry — this assistant isn't available to you.")
        return

    conversation_id = f"tg:{chat_id}"
    set_conversation(conversation_id)
    try:
        raw = store.load_history(conversation_id)
        view = await compact(conversation_id, raw)  # summary note + recent tail
        result = await run_turn(text, view, conversation_id=conversation_id)
        # Persist only this turn's new messages (the summary note is synthetic).
        store.append_messages(conversation_id, result.history[len(view):])
    except Exception as exc:
        # Never leave the user hanging on an error (bad key, out of credits, etc.).
        print("turn error:", exc, flush=True)
        await send(chat_id, "Sorry — I hit an error handling that. Try again in a moment.")
        return

    # Let the voice layer choose silence: only send a non-empty reply.
    if result.reply and result.reply.strip():
        await send(chat_id, result.reply)

    # Learn from the conversation in the background — never blocks the reply.
    asyncio.create_task(_reflect_bg(conversation_id))


async def _reflect_bg(conversation_id: str) -> None:
    try:
        learned = await maybe_reflect(conversation_id)
        if learned:
            # Log metadata only — never the learned content in plaintext.
            keys = [item.get("canonical_key") or item.get("kind") for item in learned]
            print(f"[reflect] stored {len(learned)} belief(s): {keys}", flush=True)
    except Exception as exc:
        print("reflect error:", exc, flush=True)


async def run_polling() -> None:
    """Long-poll getUpdates and dispatch. For local dev — no public URL needed.

    (Use either polling OR the webhook, never both — Telegram rejects getUpdates
    while a webhook is set.)
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    print("Telegram long-polling started. Message your bot. Ctrl-C to stop.")
    offset: int | None = None
    # read timeout must exceed the long-poll timeout; both errors are tolerated.
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=65.0)) as client:
        while True:
            params: dict = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            try:
                resp = await client.get(_API.format(token=token, method="getUpdates"), params=params)
                updates = resp.json().get("result", [])
            except (httpx.HTTPError, ValueError) as exc:
                # Network hiccup / timeout / bad JSON — log and keep polling.
                print("poll fetch error, retrying:", exc, flush=True)
                await asyncio.sleep(3)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                parsed = extract(upd)
                if parsed:
                    print(f"← [chat {parsed[0]}] {parsed[1]!r}", flush=True)
                try:
                    await handle_update(upd)
                    if parsed:
                        print(f"→ [chat {parsed[0]}] replied", flush=True)
                except Exception as exc:  # one bad message shouldn't kill the poller
                    print("handler error:", exc, flush=True)
