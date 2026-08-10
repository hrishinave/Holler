# client/ — onboarding UI (planned)

The localhost onboarding experience: clone the repo, run it, open the browser,
and click **Connect Gmail** / **Connect Google Calendar** to authorize the
services via Composio's OAuth flow — no pasting entity IDs or CLI OAuth.

**Status:** not built yet. This is a post-v1 (hosting phase) deliverable. v1 is
the core agent loop + real tools, driven from a REPL under `../server/`.

**Planned stack:** React/Next, talking to the FastAPI backend in `../server/`
(connection-initiate + OAuth-callback endpoints).
