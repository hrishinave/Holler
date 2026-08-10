"""Verification for llm.chat() retry/backoff. Deterministic — a fake client
raises real openai errors, and sleeps are stubbed so nothing actually waits.

Run:  uv --directory server run python scripts/verify_llm_retry.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import llm  # noqa: E402
from openai import APIStatusError, RateLimitError  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  {'✓' if cond else '✗'} {label}")


def _rate_limit(message: str) -> RateLimitError:
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return RateLimitError(message, response=resp, body=None)


def _server_error() -> APIStatusError:
    resp = httpx.Response(503, request=httpx.Request("POST", "http://x"))
    return APIStatusError("upstream boom", response=resp, body=None)


class FakeResp:
    def model_dump(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


class FakeClient:
    """Raises a scripted sequence of errors, then returns a response."""

    def __init__(self, errors):
        self._errors = list(errors)
        self.calls = 0
        self.chat = self  # so client.chat.completions.create resolves
        self.completions = self

    async def create(self, **kwargs):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return FakeResp()


async def main():
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    llm.asyncio.sleep = fake_sleep  # never actually wait

    print("1) helpers parse the server's signals")
    check("suggested delay parsed from 'retry in 52.0s'",
          llm._suggested_delay(_rate_limit("Please retry in 52.001s")) == 52.001)
    check("retryDelay form parsed", llm._suggested_delay(_rate_limit("retryDelay: '30s'")) == 30.0)
    check("no delay -> None", llm._suggested_delay(_rate_limit("nothing here")) is None)
    check("per-minute is not daily",
          not llm._is_daily_quota(_rate_limit("GenerateRequestsPerMinutePerProjectPerModel")))
    check("per-day detected", llm._is_daily_quota(_rate_limit("...PerDay quota...")))

    print("2) per-minute 429: retries then succeeds, honoring the delay")
    slept.clear()
    llm._client = FakeClient([
        _rate_limit("PerMinute limit. Please retry in 5s"),
        _rate_limit("PerMinute limit. Please retry in 5s"),
    ])
    out = await llm.chat([{"role": "user", "content": "hi"}])
    check("eventually returns a result", out["choices"][0]["message"]["content"] == "ok")
    check("called three times (2 fails + success)", llm._client.calls == 3)
    check("waited the suggested 5s each time", slept == [5.0, 5.0])

    print("3) per-day 429: fails fast, no retries, no waiting")
    slept.clear()
    llm._client = FakeClient([_rate_limit("free_tier PerDay quota exceeded")])
    raised = False
    try:
        await llm.chat([{"role": "user", "content": "hi"}])
    except RateLimitError:
        raised = True
    check("raised immediately", raised)
    check("only one attempt", llm._client.calls == 1)
    check("did not sleep", slept == [])

    print("4) 5xx server error is retried")
    slept.clear()
    llm._client = FakeClient([_server_error()])
    out = await llm.chat([{"role": "user", "content": "hi"}])
    check("recovered after a 503", out["choices"][0]["message"]["content"] == "ok")
    check("retried once", llm._client.calls == 2)

    print("5) gives up after the attempt budget")
    llm._client = FakeClient([_rate_limit("retry in 1s")] * 10)  # never succeeds
    gave_up = False
    try:
        await llm.chat([{"role": "user", "content": "hi"}])
    except RateLimitError:
        gave_up = True
    check("raised after exhausting attempts", gave_up)
    check("attempted exactly _MAX_ATTEMPTS times", llm._client.calls == llm._MAX_ATTEMPTS)

    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
