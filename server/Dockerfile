# Lean image for the always-on assistant process (Telegram poller + proactivity
# loops). Built from server/ as the context. Secrets are injected at runtime via
# env_file — never baked into the image (see .dockerignore excluding .env).

FROM python:3.11-slim

# uv for a fast, reproducible install from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps first (cached layer) — only busts when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Then the code.
COPY . .

# Use the venv's interpreter for everything.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Default: long-poll Telegram + run the scheduler/email-monitor. Needs no public
# URL. To run the FastAPI webhook instead, override in docker-compose.yml:
#   command: uvicorn api.app:app --host 0.0.0.0 --port 8000
CMD ["python", "scripts/telegram_poll.py"]
