"""The embeddings seam — dense vectors for document retrieval.

Provider-agnostic, like ``llm.chat``: any OpenAI-compatible ``/embeddings``
endpoint via ``EMBED_BASE_URL`` + ``EMBED_API_KEY`` + ``EMBED_MODEL``. Kept
separate from the chat provider because OpenRouter (our chat default) doesn't
serve embeddings; the default here is Google Gemini's free embeddings.

Tests inject a fake embedder with ``set_embedder`` — no network, no key.
"""

from __future__ import annotations

from typing import Callable

from openai import AsyncOpenAI

from config import settings

_client: AsyncOpenAI | None = None
# A test/override hook: a sync function text-list -> list of vectors.
_embedder: Callable[[list[str]], list[list[float]]] | None = None


def set_embedder(fn: Callable[[list[str]], list[list[float]]] | None) -> None:
    """Inject a deterministic embedder for tests (or reset to the real one)."""
    global _embedder
    _embedder = fn


def _client_or_init() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=settings.EMBED_BASE_URL, api_key=settings.EMBED_API_KEY)
    return _client


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into vectors. Empty input -> empty output."""
    if not texts:
        return []
    if _embedder is not None:
        return _embedder(texts)
    resp = await _client_or_init().embeddings.create(model=settings.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


async def embed_one(text: str) -> list[float]:
    out = await embed([text])
    return out[0] if out else []
