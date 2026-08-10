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
- [ ] Retry/backoff on OpenRouter 429/5xx; friendly handling of 402 (out of credits).
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

## Onboarding (if not fully covered by the Telegram/hosting phase)
- [ ] `client/` React onboarding UI — Connect Gmail/Calendar via Composio OAuth.
- [ ] Composio connection status checks + re-auth flow.
