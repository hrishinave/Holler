"""The web_search tool (Tavily-backed).

Read-only, so un-gated. Tavily is LLM-oriented: it returns relevance-ranked
snippets plus an optional synthesized ``answer``, which is often enough on its
own and keeps what we feed back to the model compact.

The client is lazy + injectable (``set_client``) so tests run without the SDK,
a key, or a network. The handler is sync; ``execute_tool`` runs it in a thread.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from config import settings
from schemas import ToolResult, ToolSpec, WebSearchResponse, WebSearchResult

_client: Any | None = None


def set_client(client: Any | None) -> None:
    """Inject (or reset) the Tavily client — used by tests."""
    global _client
    _client = client


def _get_client() -> Any:
    global _client
    if _client is None:
        from tavily import TavilyClient  # lazy: only needed when the tool runs

        _client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    return _client


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="What to search the web for.")
    max_results: int = Field(5, description="How many results to return (1-10).")


def _web_search(query: str, max_results: int = 5) -> ToolResult:
    n = max(1, min(int(max_results), 10))
    raw = _get_client().search(query=query, max_results=n, include_answer=True)
    results = [
        WebSearchResult(
            title=r.get("title"),
            url=r.get("url"),
            content=r.get("content"),
            score=r.get("score"),
        )
        for r in (raw.get("results") or [])
        if isinstance(r, dict)
    ]
    response = WebSearchResponse(query=query, answer=raw.get("answer"), results=results)
    return ToolResult.ok(data=response.model_dump())


SPEC = ToolSpec.from_model(
    name="web_search",
    description="Search the web for current information, facts, or research. Read-only.",
    args_model=WebSearchArgs,
    handler=_web_search,
    destructive=False,
)
