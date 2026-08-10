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

    # --- BYOK: any OpenAI-compatible LLM endpoint ---------------------------
    # A provider is just a base URL + key + model name; the code never changes.
    # Default: Google Gemini's OpenAI-compat endpoint (direct key, generous free
    # tier). For OpenRouter: LLM_BASE_URL=https://openrouter.ai/api/v1 and a
    # slug MODEL like "google/gemini-3.6-flash".
    MODEL: str = "gemini-3.6-flash"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Cap output tokens — keeps replies terse and bounds cost.
    MAX_TOKENS: int = 2048

    # --- Composio (calendar + gmail) ----------------------------------------
    COMPOSIO_API_KEY: str = ""
    # The Composio entity whose Google Calendar + Gmail are connected.
    COMPOSIO_ENTITY_ID: str = ""

    # --- Web search (Tavily) -------------------------------------------------
    TAVILY_API_KEY: str = ""

    # --- Telegram ------------------------------------------------------------
    TELEGRAM_BOT_TOKEN: str = ""
    # Comma-separated Telegram chat ids allowed to use the bot. EMPTY = allow
    # everyone — UNSAFE for a bot wired to your real Gmail/Calendar, so set this.
    TELEGRAM_ALLOWED_CHAT_IDS: str = ""

    # --- Storage -------------------------------------------------------------
    # SQLite file for the conversation log (created on first use).
    DB_PATH: str = str(Path(__file__).resolve().parent / "data" / "agent.db")

    # --- Assistant defaults --------------------------------------------------
    # Fallback zone for naive event times, used only if calendar auto-detect
    # fails (see the timezone strategy in CLAUDE.md).
    HOME_TIMEZONE: str = "America/Chicago"


settings = Settings()
