"""Streaming agent loop with tool use, event bus, and injection protection."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.events.bus import EventBus
    from agent.tools.registry import ToolRegistry


def _extract_plan(text: str) -> str | None:
    """Return the content inside the first <plan>...</plan> block, or None."""
    match = re.search(r"<plan>(.*?)</plan>", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def make_client(cfg: "Config") -> AsyncOpenAI:
    from agent.config.config import OPENROUTER_API_KEY

    return AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=OPENROUTER_API_KEY or "no-key",
        default_headers={
            "HTTP-Referer": "https://github.com/my-coding-agent",
            "X-Title": "my-coding-agent",
        },
    )


def _merge_tool_call_chunks(acc: list[dict], deltas: list) -> list[dict]:
    """Accumulate streaming tool_call delta chunks into complete tool call dicts."""
    for delta in deltas:
        idx = delta.index
        while len(acc) <= idx:
            acc.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        if delta.id:
            acc[idx]["id"] = delta.id
        if delta.function:
            if delta.function.name:
                acc[idx]["function"]["name"] += delta.function.name
            if delta.function.arguments:
                acc[idx]["function"]["arguments"] += delta.function.arguments
    return acc


async def run_turn(
    client: AsyncOpenAI,
    messages: list[dict],
    cfg: "Config",
    bus: "EventBus",
    registry: "ToolRegistry",
    _check_plan: bool = True,
) -> None:
    """Run one full agent turn (may recurse if tool calls are made).

    Mutates *messages* in place with the assistant reply and tool results.
    Emits events on *bus* throughout.
    """
    from agent.events.bus import Event
    from agent.events.types import (
        ERROR,
        MESSAGE_ASSISTANT,
        PLAN_APPROVED,
        PLAN_PROPOSED,
        STREAM_DELTA,
        TOOL_AFTER,
        TOOL_BEFORE,
        TURN_END,
        TURN_START,
    )

    await bus.emit(Event(TURN_START, {"message_count": len(messages)}))

    # --- Retry loop for transient API errors ---
    for attempt in range(cfg.max_retries):
        try:
            stream = await client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                tools=registry.schemas() or None,
                stream=True,
                max_tokens=8192,
            )
            break
        except RateLimitError as e:
            resp = getattr(e, "response", None)
            retry_after = int(resp.headers.get("retry-after", 30)) if resp else 30
            if attempt < cfg.max_retries - 1:
                await asyncio.sleep(retry_after)
                continue
            await bus.emit(Event(ERROR, {"error": str(e)}))
            return
        except APIConnectionError as e:
            if attempt < cfg.max_retries - 1:
                await asyncio.sleep(5)
                continue
            await bus.emit(Event(ERROR, {"error": str(e)}))
            return
        except APIStatusError as e:
            await bus.emit(Event(ERROR, {"error": str(e)}))
            return

    # --- Stream the response ---
    assistant_text = ""
    tool_calls_acc: list[dict] = []
    finish_reason = None
    usage: dict = {}

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue

        delta = choice.delta
        finish_reason = choice.finish_reason or finish_reason

        if delta.content:
            assistant_text += delta.content
            await bus.emit(Event(STREAM_DELTA, {"text": delta.content}))

        if delta.tool_calls:
            tool_calls_acc = _merge_tool_call_chunks(tool_calls_acc, delta.tool_calls)

        if hasattr(chunk, "usage") and chunk.usage:
                details = getattr(chunk.usage, "prompt_tokens_details", None)
                cached = getattr(details, "cached_tokens", 0) or 0
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "cached_tokens": cached,
                }

    # Append the assistant message (preserving tool_calls for the API)
    assistant_msg: dict = {"role": "assistant", "content": assistant_text or None}
    if tool_calls_acc:
        assistant_msg["tool_calls"] = tool_calls_acc
    messages.append(assistant_msg)

    await bus.emit(Event(MESSAGE_ASSISTANT, {"text": assistant_text, "usage": usage}))

    # --- Plan approval gate ---
    # Phase 1 (ideal): model outputs <plan> with NO tool calls — pause, get approval,
    #                  then inject "proceed" so the model continues in Phase 2.
    # Phase 2 fallback: model included tool calls alongside <plan> — intercept before
    #                   executing them so the user can still approve.
    if _check_plan and cfg.plan_mode:
        plan_text = _extract_plan(assistant_text)
        if plan_text:
            plan_event = await bus.emit(
                Event(PLAN_PROPOSED, {"plan": plan_text, "cancelled": False})
            )
            if plan_event.data.get("cancelled"):
                await bus.emit(Event(TURN_END, {"usage": usage}))
                return
            updated_plan = plan_event.data.get("updated_plan", plan_text)
            await bus.emit(Event(PLAN_APPROVED, {"plan": updated_plan}))

            if not tool_calls_acc:
                # Phase 1 path: no tool calls yet — inject approval and let model proceed
                messages.append({
                    "role": "user",
                    "content": "Plan approved. Please proceed with execution.",
                })
                await run_turn(client, messages, cfg, bus, registry, _check_plan=False)
                return
            # else: fall through to execute the tool calls that came with the plan

    if not tool_calls_acc:
        await bus.emit(Event(TURN_END, {"usage": usage}))
        return

    # --- Execute tool calls (in parallel) ---
    async def execute_tool(tc: dict) -> dict:
        tool_name = tc["function"]["name"]
        try:
            arguments = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {}

        # Emit tool.before — listeners may cancel or mutate input
        before_event = await bus.emit(
            Event(TOOL_BEFORE, {
                "tool": tool_name,
                "input": arguments,
                "call_id": tc["id"],
                "cancelled": False,
            })
        )

        if before_event.data.get("cancelled"):
            content = "<tool_result name=\"{name}\">\nCancelled by user.\n</tool_result>".format(
                name=tool_name
            )
        else:
            if "updated_input" in before_event.data:
                arguments = before_event.data["updated_input"]

            start = time.monotonic()
            content = await registry.dispatch(tool_name, arguments)
            duration_ms = int((time.monotonic() - start) * 1000)

            await bus.emit(Event(TOOL_AFTER, {
                "tool": tool_name,
                "call_id": tc["id"],
                "output": content,
                "duration_ms": duration_ms,
            }))

        return {"role": "tool", "tool_call_id": tc["id"], "content": content}

    tool_results = await asyncio.gather(*[execute_tool(tc) for tc in tool_calls_acc])
    messages.extend(tool_results)

    # Recurse until the model stops calling tools (skip plan check on inner turns)
    await run_turn(client, messages, cfg, bus, registry, _check_plan=False)
