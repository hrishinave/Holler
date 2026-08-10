"""Prompt loading. Prompts are plain Markdown files in this directory so voice
can be tuned without touching code."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Return the text of ``<name>.md`` from this directory (cached)."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
