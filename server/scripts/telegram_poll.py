"""Local Telegram runner — long-polling for messages AND the proactivity
scheduler, so reminders actually fire. No public URL needed.

Run:  uv --directory server run python scripts/telegram_poll.py

Requires TELEGRAM_BOT_TOKEN in server/.env. For production, use the FastAPI
webhook (api.app), which runs the same scheduler via its lifespan.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from channels.telegram import run_polling  # noqa: E402
from proactivity.scheduler import Scheduler  # noqa: E402


async def main() -> None:
    scheduler = Scheduler()
    await scheduler.start()  # fires due reminders in the background
    print("Proactivity scheduler started.")
    try:
        await run_polling()  # handles incoming messages (blocks)
    finally:
        await scheduler.stop()


if __name__ == "__main__":
    asyncio.run(main())
