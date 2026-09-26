"""Memory tools — remember and forget facts that persist across sessions.

These tools let the agent explicitly save or remove project/user facts during
a session. Facts are stored in ~/.agent/memory/<project_hash>.json and are
injected into every future session's system prompt as "## Remembered context".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# cwd is injected by the registry when dispatching
_CWD: Path | None = None


def _remember(arguments: dict[str, Any], cwd: Path) -> str:
    from agent.memory.auto import _load_existing, _save

    key = str(arguments.get("key", "")).strip()
    value = str(arguments.get("value", "")).strip()
    if not key or not value:
        return "Error: both key and value are required"

    memories = _load_existing(cwd)
    memories[key] = value
    _save(cwd, memories)
    return f"Remembered: {key} = {value}"


def _forget(arguments: dict[str, Any], cwd: Path) -> str:
    from agent.memory.auto import delete_memory

    key = str(arguments.get("key", "")).strip()
    if not key:
        return "Error: key is required"

    removed = delete_memory(cwd, key)
    return f"Forgotten: {key}" if removed else f"Key not found: {key}"


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a fact to persistent memory so it is available in every future session. "
                "Use this whenever the user tells you something to remember, states a preference, "
                "or asks you to store information (e.g. their name, preferred library, project conventions). "
                "Facts are injected into the system prompt of all future sessions for this project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short label for the fact, e.g. 'user_name', 'preferred_http_library'.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The fact to remember, 1-2 sentences max.",
                    },
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": "Remove a previously remembered fact by its key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The key to remove.",
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
        },
    },
]

HANDLERS: dict[str, Any] = {
    "remember": _remember,
    "forget": _forget,
}
