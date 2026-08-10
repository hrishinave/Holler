"""Web search contracts (the shape ``agent/tools/web_search.py`` emits)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WebSearchResult(BaseModel):
    title: str | None = None
    url: str | None = None
    content: str | None = None  # Tavily's extracted, relevance-ranked snippet
    score: float | None = None


class WebSearchResponse(BaseModel):
    query: str
    # Tavily's synthesized short answer, when available — often enough on its own.
    answer: str | None = None
    results: list[WebSearchResult] = Field(default_factory=list)
