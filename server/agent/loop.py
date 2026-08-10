"""The core agent loop.

An agent is a loop, not a plan: call the model, run whatever tools it asks for,
feed the results back, repeat — until the model replies with plain text (no tool
calls) or we hit the iteration budget. Reacting to each tool result is what makes
"find the event, then delete it" work without any up-front DAG.

The destructive-action gate lives here (code, not prompt): a tool flagged
``destructive`` is refused with a ``needs_confirmation`` result unless the user
authorized it in their own message this turn.
"""

from __future__ import annotations

import json

from gate import is_authorized
from llm import chat
from schemas import StopReason, ToolCall, ToolResult, TurnResult

from .prompts import load_prompt
from .tools.registry import TOOLS, TOOL_SCHEMAS, execute_tool

MAX_ITERS = 8


def system_prompt() -> str:
    """Voice + execution, concatenated into one system prompt (one model call)."""
    return load_prompt("voice") + "\n\n---\n\n" + load_prompt("execution")


def _assistant_msg(msg: dict) -> dict:
    """A clean assistant message to append to the transcript.

    We keep only the fields the API needs on the way back in — some providers
    emit null ``refusal``/``function_call`` fields that can trip a re-send.
    """
    out: dict = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


async def run_turn(
    user_text: str,
    history: list | None = None,
    *,
    authorized_destructive: bool = False,
) -> TurnResult:
    """Run one user turn to completion. Returns the reply + updated transcript."""
    messages: list = list(history or []) + [{"role": "user", "content": user_text}]
    tools_used: list[str] = []

    for i in range(1, MAX_ITERS + 1):
        resp = await chat(messages, tools=TOOL_SCHEMAS, system=system_prompt())
        msg = resp["choices"][0]["message"]
        messages.append(_assistant_msg(msg))

        raw_calls = msg.get("tool_calls") or []
        if not raw_calls:
            # No tools requested -> this text is the reply.
            return TurnResult(
                reply=msg.get("content") or "",
                history=messages,
                iterations=i,
                tools_used=tools_used,
                stop_reason=StopReason.COMPLETED,
            )

        for raw in raw_calls:
            call = ToolCall.from_openai(raw)
            tools_used.append(call.name)
            spec = TOOLS.get(call.name)

            if spec is not None and spec.is_destructive(call.arguments) and not authorized_destructive:
                # Hard gate: refuse; make the model come back and ask the user.
                result = ToolResult.needs_confirmation(
                    f"{call.name} is destructive and the user has not confirmed. "
                    "Ask them to confirm before running it."
                ).model_dump()
            else:
                result = await execute_tool(call.name, call.arguments)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return TurnResult(
        reply="Ran out of steps before finishing that.",
        history=messages,
        iterations=MAX_ITERS,
        tools_used=tools_used,
        stop_reason=StopReason.MAX_ITERS,
    )


__all__ = ["run_turn", "system_prompt", "is_authorized", "MAX_ITERS"]
