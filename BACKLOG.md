# Backlog — after the initial plan

Enhancements deferred until Phases 4–6 (Telegram, proactivity, hardening) are done.
These are NOT in the initial plan; they're things we flagged while building.

## Memory
- [x] Long-term / semantic memory (manual) — `memory/facts.py`: durable facts +
      hard email skip/flag rules, injected into the system prompt and email triage.
      Tools: remember / forget / list_memory / email_rule. **Built.** Limitation:
      only learns when explicitly told.
- [~] Autonomous learning ("reflection") — the bot extracts durable facts from the
      natural flow of conversation on its own, no "remember" needed. Debounced
      background pass over recent messages. **In progress** — the real win; makes
      the manual version above not feel superficial.
- [ ] Implement `memory/summarize.compact` for real — summarize old turns into a
      system note once a conversation grows; never split an assistant `tool_calls`
      message from its `tool` results. (Transcript still replays in full every turn.)
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

## Robustness / ops
- [ ] Migrate `scripts/verify_*.py` into a proper pytest suite.
- [ ] Structured logging + clearer error surfaces to the user.
- [ ] Cost / rate-limit guardrails.

## Onboarding (if not fully covered by the Telegram/hosting phase)
- [ ] `client/` React onboarding UI — Connect Gmail/Calendar via Composio OAuth.
- [ ] Composio connection status checks + re-auth flow.
