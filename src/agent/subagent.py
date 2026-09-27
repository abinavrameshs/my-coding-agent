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
_PARALLEL_TOOL_NAME = "spawn_parallel"

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


PARALLEL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _PARALLEL_TOOL_NAME,
        "description": (
            "Execute multiple independent plan items concurrently using subagents. "
            "Items with declared dependencies run after their prerequisites. "
            "Items touching the same files are automatically serialised. Max depth 3."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "List of tasks to execute in parallel.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique id for this task."},
                            "prompt": {"type": "string", "description": "Task description for the subagent."},
                            "context": {"type": "string", "description": "Optional shared context."},
                            "deps": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Ids of tasks that must complete before this one.",
                            },
                            "files": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Files this task will modify (used to detect conflicts).",
                            },
                        },
                        "required": ["id", "prompt"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["tasks"],
            "additionalProperties": False,
        },
    },
}


async def run_parallel(
    tasks: list[dict[str, Any]],
    deps: dict[str, list[str]] | None,
    parent_bus: "EventBus",
    registry: "ToolRegistry",
    cfg: "Config",
    depth: int,
    cwd: Any,
) -> str:
    """Run a batch of tasks via subagents, respecting dependencies and file conflicts."""
    from agent.events.bus import Event
    from agent.events.payloads import ParallelBatchPayload
    from agent.events.types import PARALLEL_BATCH_END, PARALLEL_BATCH_START
    from agent.parallel import group_by_files, normalize_deps, plan_batches

    if depth >= MAX_DEPTH:
        return f"Error: subagent depth limit ({MAX_DEPTH}) exceeded."

    # Normalise and validate tasks
    valid: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("id", "")
        prompt = t.get("prompt", "")
        if not tid or not prompt or tid in seen_ids:
            continue
        seen_ids.add(tid)
        valid.append(t)

    if not valid:
        return "Error: no valid tasks provided."

    item_ids = [t["id"] for t in valid]
    task_by_id = {t["id"]: t for t in valid}
    file_hints = {t["id"]: t.get("files") or [] for t in valid}

    # Build dependency map from task "deps" fields or caller-supplied deps
    raw_deps: dict[str, list[str]] = deps or {t["id"]: t.get("deps") or [] for t in valid}
    norm_deps = normalize_deps(raw_deps, item_ids)

    # Compute batches
    max_width = cfg.max_parallel_subagents if cfg.parallel_planning else 1
    batches = plan_batches(item_ids, norm_deps, max_width=max_width)
    batches = group_by_files(batches, file_hints)

    all_task_ids = [t["id"] for t in valid]
    await parent_bus.emit(Event(PARALLEL_BATCH_START, ParallelBatchPayload(tasks=all_task_ids)))

    results: dict[str, str] = {}
    for batch in batches:
        batch_coros = []
        for tid in batch:
            t = task_by_id[tid]
            batch_coros.append(
                run_subagent(
                    t["prompt"],
                    t.get("context"),
                    parent_bus,
                    registry,
                    cfg,
                    depth,
                    cwd,
                )
            )
        import asyncio
        batch_results = await asyncio.gather(*batch_coros)
        for tid, result in zip(batch, batch_results):
            results[tid] = result

    await parent_bus.emit(Event(PARALLEL_BATCH_END, ParallelBatchPayload(tasks=all_task_ids)))

    # Aggregate results in original order
    parts = []
    for t in valid:
        parts.append(f"### Task {t['id']}\n{results.get(t['id'], '(no result)')}")
    return "\n\n".join(parts)


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

    from agent.events.bus import Event, EventBus
    from agent.events.payloads import SubagentEndPayload, SubagentStartPayload
    from agent.events.types import SUBAGENT_END, SUBAGENT_START
    from agent.loop import make_client, run_turn
    from agent.memory.loader import assemble_system_prompt

    if depth >= MAX_DEPTH:
        return f"Error: subagent depth limit ({MAX_DEPTH}) exceeded."

    subagent_id = uuid.uuid4().hex[:8]
    await parent_bus.emit(Event(SUBAGENT_START, SubagentStartPayload(subagent_id=subagent_id, prompt=prompt)))

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
    sub_registry = _SubagentRegistry(registry, parent_bus, cfg, cwd, depth + 1)

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

    await parent_bus.emit(Event(SUBAGENT_END, SubagentEndPayload(subagent_id=subagent_id, result=result[:500])))
    return result


class _SubagentRegistry:
    """Thin wrapper around ToolRegistry that intercepts spawn_subagent calls."""

    def __init__(
        self,
        inner: "ToolRegistry",
        parent_bus: "EventBus",
        cfg: "Config",
        cwd: Any,
        depth: int,
    ) -> None:
        self._inner = inner
        self._parent_bus = parent_bus
        self._cfg = cfg
        self._cwd = cwd
        self._depth = depth

    def _spawn_schemas(self) -> list[dict]:
        """Return the extra spawn schemas if below max depth."""
        if self._depth < MAX_DEPTH:
            return [SCHEMA, PARALLEL_SCHEMA]
        return []

    def schemas(self) -> list[dict]:
        return self._inner.schemas() + self._spawn_schemas()

    def schemas_for(self, hint: str) -> list[dict]:
        return self._inner.schemas_for(hint) + self._spawn_schemas()

    async def dispatch(self, tool_name: str, arguments: dict) -> str:
        if tool_name == _TOOL_NAME:
            prompt = arguments.get("prompt", "")
            context = arguments.get("context")
            result = await run_subagent(
                prompt, context, self._parent_bus, self._inner,
                self._cfg, self._depth, self._cwd,
            )
            return f"<tool_result name=\"{_TOOL_NAME}\">\n{result}\n</tool_result>"
        if tool_name == _PARALLEL_TOOL_NAME:
            tasks = arguments.get("tasks", [])
            result = await run_parallel(
                tasks, None, self._parent_bus, self._inner,
                self._cfg, self._depth, self._cwd,
            )
            return f"<tool_result name=\"{_PARALLEL_TOOL_NAME}\">\n{result}\n</tool_result>"
        return await self._inner.dispatch(tool_name, arguments)

    # Delegate everything else to inner registry
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
