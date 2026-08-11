# Backlog — after the initial plan

Enhancements deferred until Phases 4–6 (Telegram, proactivity, hardening) are done.
These are NOT in the initial plan; they're things we flagged while building.

## Memory
- [x] Long-term / semantic memory (manual) — `memory/facts.py`: durable facts +
      hard email skip/flag rules, injected into the system prompt and email triage.
      Tools: remember / forget / list_memory / email_rule. **Built.** Limitation:
      only learns when explicitly told.
- [x] Autonomous learning ("reflection") — `memory/reflect.py`: debounced
      background pass extracts durable facts from conversation on its own, no
      "remember" needed. **Built.**
- [x] Compaction — `memory/summarize.compact`: rolling, non-destructive. Old turns
      folded into a cached summary note, recent turns kept verbatim, cuts only on
      user-message boundaries (never splits a tool exchange). **Built.** Fixes the
      O(n^2) full-transcript replay.
- [ ] Persist-summary is done; still open: make compaction run in the background
      (off the reply path) instead of inline on the turn that crosses the threshold.
- [ ] Wrap the sqlite calls in `asyncio.to_thread` under the async server.

## Model / answer quality
- [ ] Prompt nudge: when blocking time for a specific event, anchor the block to
      the event's actual start time (the SpaceX "8:30 PM vs 5:34 PM" slip).
- [x] Retry/backoff on 429/5xx in llm.chat() — honors the server's retry delay for
      per-minute limits, fails fast on per-day quota. **Built** (verify_llm_retry).
      Still open: friendly in-chat handling of 402 (out of credits) / per-day 429.
- [ ] Consider a stronger model (or per-task routing) for multi-constraint reasoning.

## Tools
- [ ] Verify `gmail_reply` threading live — confirm the arg name Composio's
      `GMAIL_SEND_EMAIL` wants for threading (`thread_id` vs `threadId`).
- [ ] `calendar_update` / move-event tool (we only have create + delete).
- [ ] Optional `gmail_trash` / `gmail_label` (reversible housekeeping).
- [ ] Timezone: in-chat override ("I'm in London this week") + travel detection (v2).

## Proactive delivery (now that there's a ProactiveEvent + notifier choke point)
- [x] `ProactiveEvent` schema + `notifier.deliver()` choke point + outbox log +
      dedup; scheduler and email monitor route through it. **Built.**
- [x] Structured inbox attention — monitors recent mail regardless of read state,
      fetches full messages, extracts meeting/action/deadline intent under an
      untrusted-content boundary, renders relevant signals through a dedicated
      proactive voice prompt, and retries failed analysis. **Built.**
- [ ] Calendar-aware meeting nudges — after a meeting request is detected, add a
      read-only availability lookup and mention the first useful opening without
      scheduling anything automatically.
- [ ] Quiet hours — suppress/defer proactive events during e.g. 11pm–7am.
- [ ] Batching / digest — coalesce several events (e.g. 3 new emails) into one
      message instead of a burst; also eases the model rate limit.
- [ ] Delivery retry from the outbox for transient send failures.
- [x] Proactive messages the user receives must enter the conversation transcript
      so they can reply to them ("accept that", "reply yes") with context. Email
      nudges now append to `memory.store` on delivery. **Built.**
- [ ] Unify this in `notifier.deliver` (the single choke point) instead of per
      source, and stop the scheduler from appending its internal trigger prompt as
      a fake `user` message (record only the user-facing assistant message).

## Hosting / deploy (Phase 6)
- [x] `Dockerfile` (uv, reproducible from `uv.lock`) + `docker-compose.yml` +
      `.dockerignore`. Poller entrypoint by default (no public URL); `.env`
      injected at runtime, DB volume-mounted, `restart: unless-stopped`. **Built
      & build-verified.**
- [x] README with quickstart, config table, and deploy instructions. **Built.**
- [ ] Production webhook path is documented (commented in compose) but not
      exercised end-to-end — needs a public host to test `setWebhook`.

## Robustness / ops
- [ ] Migrate `scripts/verify_*.py` into a proper pytest suite.
- [ ] Structured logging + clearer error surfaces to the user.
- [ ] Cost / rate-limit guardrails.

## Onboarding — in-chat OAuth connect (the priority approach)
The way Poke / folk.com do it, and the highest-leverage thing for "everyone can
fork and plug in": the bot onboards you *in the conversation*, not via a manual
setup. Ask "what can you do?" → it offers "want to connect Gmail?" → you say yes →
it drops a Composio OAuth link in chat → you click, log in with Google, done.
Natural, not forced. This supersedes the CLI-`setup` idea; the React UI below
becomes optional.

Feasibility confirmed against the installed SDK (composio 0.18.2):
- `toolkits.authorize(user_id, toolkit)` → returns a `redirect_url` to send in chat.
- `connected_accounts.wait_for_connection()` / `.list(user_id=...)` → connection status.
- Composio hosts the OAuth callback, so we run no callback server.

Work items:
- [ ] **Generalize the entity id.** Stop pinning every Composio call to the
      hardcoded `COMPOSIO_ENTITY_ID`; derive the Composio `user_id` from the current
      chat via the `current_conversation` ContextVar (see `agent/tools/_composio.py`
      `execute()`). Each user's connections live in their own namespace; a single-owner
      fork just has one. **This also removes the baked-in entity id leaking through
      `.env.example`.**
- [ ] **`connect_service` tool** (gmail | googlecalendar | outlook) → initiate + return
      the OAuth link. **`connection_status` tool** → what's connected, so the agent can
      gate actions.
- [ ] **Graceful "not connected"** — when a Gmail/Calendar tool fails for lack of a
      connection, offer the connect link instead of erroring.
- [ ] **Prompt nudge** — onboard naturally: offer to connect on "what can you do?" or
      the first time a needed service isn't connected. Never forced.
- [ ] **Auth configs / zero-setup** — default to Composio *managed auth* for Google so a
      forker needs only a Composio API key (no Google Cloud project); create/reuse a
      default auth config via `auth_configs.create` on first connect. Document the
      bring-your-own-Google-OAuth path as the alternative.
- [ ] Security: always initiate for the *authenticated* chat sender's user_id so users
      can't connect into each other's namespace.

## Onboarding — later / optional
- [ ] `client/` React onboarding UI — only if a web flow is wanted on top of in-chat.
- [ ] Re-auth flow when a connection expires or is revoked.
