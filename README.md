# personal-agent

A lean, open-source, **bring-your-own-key** personal assistant in the spirit of
[Poke](https://poke.com) — a Telegram bot that manages your Google Calendar and
Gmail, can search the web, remembers what matters, and is *proactive* (it messages
you first: reminders, important-email nudges).

It's a **single agent loop**, not a multi-agent swarm — one model call per turn.
That keeps it cheap enough to run on a free model, and small enough to read
(~2k LOC). You bring one key for any OpenAI-compatible provider (Google Gemini,
OpenRouter, a local model) and the code never changes.

## What it does

- **Calendar** — list, create, and delete events (Composio → Google Calendar).
- **Gmail** — read, draft, send, reply (Composio → Gmail).
- **Web search** — read-only research via Tavily.
- **Memory** — learns durable facts about you as you chat, and compacts long
  histories so context stays affordable.
- **Proactivity** — reminders fire on schedule; an optional inbox monitor flags
  important mail. Both run as background loops (need an always-on host).

Side-effectful actions (sending email, deleting events, inviting people) are
**gated in code** — the bot asks before it acts unless you approved in your own
message.

## Quickstart (local)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
cd server
cp .env.example .env      # then fill in your keys (see below)
uv sync
uv run python scripts/telegram_poll.py
```

This long-polls Telegram (no public URL needed) and runs the proactivity loops.
Message your bot and it replies. To try it without Telegram, use the REPL:

```bash
uv --directory server run python repl.py
```

## Configuration

Everything is env-driven (`server/.env`). The essentials:

| Var | What |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `MODEL` | Any OpenAI-compatible provider. Default is Google Gemini's free tier. |
| `COMPOSIO_API_KEY`, `COMPOSIO_ENTITY_ID` | Calendar + Gmail access via Composio. |
| `TAVILY_API_KEY` | Web search (1000 free searches/mo). |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/botfather). |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Lock the bot to your chat id(s). **Set this** — it touches your real inbox. |
| `EMAIL_MONITOR_ENABLED` | Turn on unprompted inbox nudges (off by default). |
| `HOME_TIMEZONE` | Fallback zone; the bot auto-detects from your calendar otherwise. |

See [`server/.env.example`](server/.env.example) for the full list and provider
alternatives (OpenRouter, local Ollama).

## Deploy (always-on)

Proactivity only fires while the process is up, so for real use run it on a host
that never sleeps — a small VPS, a Raspberry Pi, or a Mac set to never sleep.
Docker makes that one command:

```bash
docker compose up -d          # build + run, self-restarts on crash/reboot
docker compose logs -f        # watch it
```

The container long-polls Telegram by default (no public URL required). Your
`.env` is injected at runtime and never baked into the image; the SQLite DB is
mounted to `./server/data` so memory survives restarts.

To use the FastAPI **webhook** instead of polling (tidier at scale, needs a
public HTTPS URL), see the commented `command`/`ports` block in
[`docker-compose.yml`](docker-compose.yml), then point Telegram's `setWebhook`
at `https://<your-host>/telegram/webhook`.

## Layout

```
server/
  agent/         # the loop + prompts + tools
  channels/      # Telegram adapter
  memory/        # conversation log, facts, reflection, compaction
  proactivity/   # scheduler, email monitor, delivery
  schemas/       # Pydantic contracts
  api/app.py     # FastAPI webhook + health
  scripts/       # verify_*.py smoke tests, telegram_poll.py
client/          # (post-v1) React onboarding UI
```

## Development

Each phase ships a deterministic smoke test (mocked model/services):

```bash
uv --directory server run python scripts/verify_memory.py
uv --directory server run python scripts/verify_compaction.py
# ...and the other verify_*.py
```
