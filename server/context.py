"""Ambient per-turn context.

``run_turn`` is pure (it takes a history list, returns one), but some tools need
to know *which conversation* they're running in — e.g. ``trigger_create`` has to
attach a reminder to the right chat. Rather than thread a channel object through
the loop and every tool signature, the channel sets this ContextVar before
calling ``run_turn``; tools read it. Defaults to "" when there's no context.
"""

from __future__ import annotations

from contextvars import ContextVar

current_conversation: ContextVar[str] = ContextVar("current_conversation", default="")


def set_conversation(conversation_id: str) -> None:
    current_conversation.set(conversation_id)


def get_conversation() -> str:
    return current_conversation.get()
