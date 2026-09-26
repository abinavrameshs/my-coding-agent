"""Subagent support — spawn isolated agent loops from within a parent turn.

Each subagent runs with:
- Its own fresh MessageHistory (system prompt + one user message)
- Its own EventBus (events do not flood the parent bus)
- The parent's ToolRegistry and Config
- A depth counter to prevent infinite recursion (max depth 3)

The parent receives SUBAGENT_START + SUBAGENT_END events with the result.
Multiple spawn_subagent calls in one parent tool-call batch execute in parallel
via asyncio.gather (the dispatcher calls this coroutine in parallel naturally).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.events.bus import EventBus
    from agent.tools.registry import ToolRegistry

MAX_DEPTH = 3
_TOOL_NAME = "spawn_subagent"

SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": (
            "Spawn an isolated subagent to complete a focused sub-task. "
            "The subagent runs its own tool-calling loop and returns a text result. "
            "Multiple calls in one turn execute in parallel. Max depth 3."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task for the subagent to complete. Be specific.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional extra background the subagent should know.",
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
}


async def run_subagent(
    prompt: str,
    context: str | None,
    parent_bus: "EventBus",
    registry: "ToolRegistry",
    cfg: "Config",
    depth: int,
    cwd: Any,  # Path
) -> str:
    """Run one subagent and return its final text output."""
    from pathlib import Path

    from agent.events.bus import Event, EventBus
    from agent.events.types import SESSION_END, SESSION_START, SUBAGENT_END, SUBAGENT_START
    from agent.loop import make_client, run_turn
    from agent.memory.loader import assemble_system_prompt

    if depth >= MAX_DEPTH:
        return f"Error: subagent depth limit ({MAX_DEPTH}) exceeded."

    subagent_id = uuid.uuid4().hex[:8]
    await parent_bus.emit(Event(SUBAGENT_START, {"subagent_id": subagent_id, "prompt": prompt}))

    # Build isolated message history
    blocks = assemble_system_prompt(cwd, cfg)
    has_cache = any("cache_control" in b for b in blocks)
    sys_content = blocks if has_cache else "\n\n".join(b["text"] for b in blocks)
    user_content = f"{context}\n\n{prompt}" if context else prompt
    messages: list[dict] = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": user_content},
    ]

    # Subagent gets its own bus so its events don't pollute parent display
    sub_bus = EventBus()
    client = make_client(cfg)

    # Pass depth+1 down via a patched registry wrapper
    sub_registry = _SubagentRegistry(registry, parent_bus, sub_bus, cfg, cwd, depth + 1)

    try:
        await run_turn(client, messages, cfg, sub_bus, sub_registry, _check_plan=False)
    except Exception as e:
        result = f"Subagent error: {e}"
    else:
        # Collect the last assistant text from messages
        result = next(
            (m["content"] for m in reversed(messages) if m["role"] == "assistant" and m.get("content")),
            "(no response)",
        )
        if not isinstance(result, str):
            result = str(result)

    await parent_bus.emit(Event(SUBAGENT_END, {"subagent_id": subagent_id, "result": result[:500]}))
    return result


class _SubagentRegistry:
    """Thin wrapper around ToolRegistry that intercepts spawn_subagent calls."""

    def __init__(
        self,
        inner: "ToolRegistry",
        parent_bus: "EventBus",
        sub_bus: "EventBus",
        cfg: "Config",
        cwd: Any,
        depth: int,
    ) -> None:
        self._inner = inner
        self._parent_bus = parent_bus
        self._cfg = cfg
        self._cwd = cwd
        self._depth = depth

    def schemas(self) -> list[dict]:
        # Include spawn_subagent if we haven't hit max depth
        base = self._inner.schemas()
        if self._depth < MAX_DEPTH:
            base = base + [SCHEMA]
        return base

    async def dispatch(self, tool_name: str, arguments: dict) -> str:
        if tool_name == _TOOL_NAME:
            prompt = arguments.get("prompt", "")
            context = arguments.get("context")
            result = await run_subagent(
                prompt, context, self._parent_bus, self._inner,
                self._cfg, self._depth, self._cwd,
            )
            return f"<tool_result name=\"{_TOOL_NAME}\">\n{result}\n</tool_result>"
        return await self._inner.dispatch(tool_name, arguments)

    # Delegate everything else to inner registry
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
