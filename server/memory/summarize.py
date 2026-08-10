"""History compaction — v1 stub.

Loading the full transcript every turn grows the context (and cost) without
bound. The eventual fix: once a conversation exceeds a threshold, summarize the
oldest turns into a single system note and keep only recent messages verbatim —
being careful never to split an assistant ``tool_calls`` message from its
``tool`` results (that produces an invalid transcript).

For v1 this is a no-op passthrough so the wiring is in place; swapping in a real
implementation later won't touch the loop or the channels.
"""

from __future__ import annotations


def compact(messages: list[dict], *, max_messages: int = 40) -> list[dict]:
    """Return the transcript to send to the model. v1: unchanged."""
    return messages
