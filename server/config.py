"""Settings, loaded from the environment (and server/.env).

This is the one place env vars are read. Everything else imports ``settings``.
The BYOK seam lives here: ``MODEL`` is an OpenRouter model slug, so swapping
models (or providers) is a one-line env change and never touches code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path so the .env resolves no matter what the working directory is.
_ENV_FILE = str(Path(__file__).resolve().parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- BYOK via OpenRouter -------------------------------------------------
    # One key, one base URL, any model by slug (see https://openrouter.ai/models).
    # Examples: "google/gemini-2.0-flash-exp:free" (free), "anthropic/claude-opus-4"
    # (paid). The ":free" suffix selects a free-tier variant when one exists.
    MODEL: str = "google/gemini-3.6-flash"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Cap output tokens. Keeps replies terse, bounds cost, and avoids OpenRouter
    # reserving the model's full context window against your credit balance (402).
    MAX_TOKENS: int = 2048

    # --- Composio (calendar + gmail) ----------------------------------------
    COMPOSIO_API_KEY: str = ""
    # The Composio entity whose Google Calendar + Gmail are connected.
    COMPOSIO_ENTITY_ID: str = ""

    # --- Web search (Tavily) -------------------------------------------------
    TAVILY_API_KEY: str = ""

    # --- Storage -------------------------------------------------------------
    # SQLite file for the conversation log (created on first use).
    DB_PATH: str = str(Path(__file__).resolve().parent / "data" / "agent.db")

    # --- Assistant defaults --------------------------------------------------
    # Fallback zone for naive event times, used only if calendar auto-detect
    # fails (see the timezone strategy in CLAUDE.md).
    HOME_TIMEZONE: str = "America/Chicago"


settings = Settings()
