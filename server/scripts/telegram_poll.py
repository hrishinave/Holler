"""Local Telegram runner (long-polling) — no public URL needed.

Run:  uv --directory server run python scripts/telegram_poll.py

Requires TELEGRAM_BOT_TOKEN in server/.env. For production, use the FastAPI
webhook (api.app) instead.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from channels.telegram import run_polling  # noqa: E402

if __name__ == "__main__":
    asyncio.run(run_polling())
