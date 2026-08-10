"""The proactivity scheduler.

A background loop that, every ``interval`` seconds, finds due triggers and fires
them: run the trigger's task through the agent loop (with the destructive gate
forced closed — proactive turns must never send mail or delete without the user
present), send the reply to the user's chat, then reschedule (recurring) or mark
done (one-shot).

``send`` and ``runner`` are injectable so tests drive it with no Telegram/model.
Firing is sequential per tick with an ``_in_flight`` guard — plenty for a single
user, and fully deterministic to test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agent.loop import run_turn
from channels import telegram
from context import set_conversation
from memory import store as mstore
from proactivity import notifier
from proactivity import store as pstore
from schemas import Trigger

_REPEAT_DELTA = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


def _next_fire(repeat: str | None, current_iso: str) -> str | None:
    """Next fire time for a recurring trigger, always in the future; None if one-shot."""
    delta = _REPEAT_DELTA.get(repeat or "")
    if delta is None:
        return None
    nxt = datetime.fromisoformat(current_iso)
    now = datetime.utcnow()
    while nxt <= now:
        nxt += delta
    return nxt.strftime("%Y-%m-%dT%H:%M:%S")


class Scheduler:
    def __init__(self, *, interval: float = 30.0, send=None, runner=None):
        self.interval = interval
        self._send = send or telegram.send
        self._runner = runner or run_turn
        self._in_flight: set[int] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # a bad tick shouldn't kill the loop
                print("scheduler tick error:", exc, flush=True)
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        """Fire all due triggers once. Safe to call directly in tests."""
        for trig in pstore.due(pstore.utcnow_iso()):
            if trig.id in self._in_flight:
                continue
            self._in_flight.add(trig.id)
            try:
                await self._fire(trig)
            finally:
                self._in_flight.discard(trig.id)

    async def _fire(self, trig: Trigger) -> None:
        try:
            set_conversation(trig.conversation_id)
            history = mstore.load_history(trig.conversation_id)
            # interactive=False: proactive turns can read/plan but never send mail
            # or delete events unprompted (no user present to approve).
            result = await self._runner(
                trig.prompt, history, conversation_id=trig.conversation_id, interactive=False
            )
            mstore.append_messages(trig.conversation_id, result.history[len(history):])

            if result.reply and result.reply.strip():
                event = notifier.make_event(
                    "trigger", trig.conversation_id, result.reply,
                    dedup_key=f"trigger:{trig.id}:{trig.next_trigger}",
                )
                await notifier.deliver(event, send=self._send)

            nxt = _next_fire(trig.repeat, trig.next_trigger)
            if nxt:
                pstore.reschedule(trig.id, nxt)
            else:
                pstore.mark_done(trig.id)
        except Exception as exc:
            # Don't let a broken trigger hot-loop: retire it.
            print(f"trigger #{trig.id} failed:", exc, flush=True)
            pstore.mark_done(trig.id)
