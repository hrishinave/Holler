"""Destructive-action approval gate.

The rule (ported from the S16 planner gate): a destructive action — deleting an
event, sending or replying to mail — must not run unless the user approved it in
*their own most recent message*. This is enforced in the loop (code), not left to
the prompt, so a model that "decides" to send anyway is still stopped.

Which tools count as destructive is declared per-tool via ``ToolSpec.destructive``
(the registry is the source of truth); this module only answers "did the user say
go?".
"""

from __future__ import annotations

import re

# Word-boundary match so "yesterday" doesn't read as "yes" and "confirmation"
# doesn't trip on "confirm" mid-word. Approval must be an actual go-ahead word.
_APPROVAL_RE = re.compile(
    r"\b(yes|yep|yeah|confirm|confirmed|approved?|go ahead|do it|send it|"
    r"go for it|proceed|please do)\b",
    re.IGNORECASE,
)


def is_authorized(user_text: str) -> bool:
    """True if the user's message contains an explicit approval phrase."""
    return bool(_APPROVAL_RE.search(user_text or ""))
