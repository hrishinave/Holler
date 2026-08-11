"""The core agent loop.

An agent is a loop, not a plan: call the model, run whatever tools it asks for,
feed the results back, repeat — until the model replies with plain text (no tool
calls) or we hit the iteration budget. Reacting to each tool result is what makes
"find the event, then delete it" work without any up-front DAG.

The destructive-action gate lives here (code, not prompt): a tool flagged
``destructive`` is not run — the loop ``propose``s it (freezing the exact args)
and asks the user. On a later turn, if the user approves, the loop executes the
*stored* args verbatim. See ``gate`` for the pending-action model.
"""

from __future__ import annotations

import json
import secrets

import gate
from llm import chat
from memory.facts import memory_block
from schemas import StopReason, ToolCall, ToolResult, TurnResult

from .prompts import load_prompt
from .tools.registry import TOOLS, TOOL_SCHEMAS, execute_tool

MAX_ITERS = 8


def system_prompt() -> str:
    """Voice + execution + what we know about the user, as one system prompt."""
    base = load_prompt("voice") + "\n\n---\n\n" + load_prompt("execution")
    known = memory_block()
    return base + ("\n\n---\n\n" + known if known else "")


def _assistant_msg(msg: dict) -> dict:
    """A clean assistant message to append to the transcript.

    We keep only the fields the API needs on the way back in — some providers
    emit null ``refusal``/``function_call`` fields that can trip a re-send.
    """
    out: dict = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def _synthetic_tool_exchange(tool: str, arguments: dict, result: dict) -> list[dict]:
    """A valid assistant(tool_call)+tool pair for an action we ran on the user's
    behalf (an approved pending action), so the model sees the outcome and the
    transcript stays well-formed for compaction/reflection."""
    call_id = "confirmed_" + secrets.token_hex(4)
    return [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": call_id, "type": "function",
                         "function": {"name": tool,
                                      "arguments": json.dumps(arguments, default=str)}}]},
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, default=str)},
    ]


async def run_turn(
    user_text: str,
    history: list | None = None,
    *,
    conversation_id: str = "",
    interactive: bool = True,
) -> TurnResult:
    """Run one user turn to completion. Returns the reply + updated transcript.

    ``interactive`` is False for unattended (proactive) runs: there is no user to
    approve anything, so destructive tools can never fire.
    """
    messages: list = list(history or []) + [{"role": "user", "content": user_text}]
    tools_used: list[str] = []

    # Approval short-circuit: if the user's message approves the pending action,
    # execute the STORED args verbatim (variant B) before the model runs, and let
    # it narrate the result. Nothing the model does this turn can alter what ran.
    if interactive and gate.reads_as_approval(user_text):
        approved = gate.take_approved(conversation_id)
        if approved is not None:
            result = await execute_tool(approved.tool, approved.arguments)
            tools_used.append(approved.tool)
            messages += _synthetic_tool_exchange(approved.tool, approved.arguments, result)

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

            if spec is not None and spec.is_destructive(call.arguments):
                # Hard gate: don't run it. Freeze the exact args and ask the user.
                if not interactive:
                    result = ToolResult.needs_confirmation(
                        f"{call.name} is destructive and this run is unattended; it cannot "
                        "be performed without the user present to approve it."
                    ).model_dump()
                else:
                    try:
                        frozen = spec.validate_args(call.arguments)
                    except Exception:
                        frozen = call.arguments
                    pending = gate.propose(conversation_id, call.name, frozen)
                    result = ToolResult.needs_confirmation(
                        "Not done yet — this needs the user's go-ahead. Show them exactly "
                        f"what will happen and ask them to confirm:\n{pending.preview}"
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


__all__ = ["run_turn", "system_prompt", "MAX_ITERS"]
