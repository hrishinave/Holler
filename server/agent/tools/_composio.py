"""Shared Composio client + execute helper for the calendar and gmail tools.

Isolated here so both toolkits talk to Composio the same way, and so tests can
inject a fake client with ``set_client`` (no SDK, no network). The credential
posture is Composio's: the OAuth token lives in their cloud, we hold only the
API key + the entity id whose Google account is connected.
"""

from __future__ import annotations

from typing import Any

from config import settings

# Bounds, so a runaway list/body can't blow up the context we feed the model.
MAX_LIST_RESULTS = 25
MAX_DESCRIPTION_CHARS = 500
MAX_BODY_CHARS = 4000

_client: Any | None = None


def set_client(client: Any | None) -> None:
    """Inject (or reset) the Composio client — used by tests."""
    global _client
    _client = client


def _get_client() -> Any:
    global _client
    if _client is None:
        from composio import Composio  # lazy: only needed when a tool actually runs

        _client = Composio(api_key=settings.COMPOSIO_API_KEY)
    return _client


def execute(slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one Composio tool and return its unwrapped ``data`` dict.

    Raises ``RuntimeError`` on a non-successful result so the tool layer surfaces
    a real failure instead of empty evidence. ``dangerously_skip_version_check``
    matches Composio's direct-execution docs (it refuses "latest" otherwise).
    """
    result = _get_client().tools.execute(
        slug,
        user_id=settings.COMPOSIO_ENTITY_ID or "default",
        arguments=arguments,
        dangerously_skip_version_check=True,
    )
    if isinstance(result, dict):
        successful = result.get("successful", result.get("success", True))
        error = result.get("error")
        data = result.get("data", result)
    else:  # SDK object shape
        successful = getattr(result, "successful", True)
        error = getattr(result, "error", None)
        data = getattr(result, "data", {})
    if not successful:
        raise RuntimeError(f"composio {slug} failed: {error or 'unknown error'}")
    return data if isinstance(data, dict) else {"result": data}


def first(mapping: dict[str, Any], *keys: str, default: Any = "") -> Any:
    """First present, non-empty value among several candidate keys."""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default
