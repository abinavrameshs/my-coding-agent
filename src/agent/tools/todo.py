"""TODO tracking tools — todo_write and todo_read.

The TODO list lives in memory for the lifetime of one REPL session.
The model calls todo_write to replace the full list, and todo_read to inspect it.
"""

from __future__ import annotations

from typing import Any

# Module-level store — one list per agent process
_items: list[dict[str, str]] = []


def reset_todos() -> None:
    """Clear the todo list (called on session start)."""
    _items.clear()


def get_todos() -> list[dict[str, str]]:
    return list(_items)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _todo_write(arguments: dict[str, Any], _cwd: Any = None) -> str:
    """Replace the full TODO list."""
    from agent.tools.files import ToolError

    raw = arguments.get("items")
    if not isinstance(raw, list):
        raise ToolError("'items' must be a list")

    _items.clear()
    for item in raw:
        if not isinstance(item, dict):
            raise ToolError("Each item must be an object with id, text, status")
        _items.append({
            "id": str(item.get("id", "")),
            "text": str(item.get("text", "")),
            "status": str(item.get("status", "pending")),
        })

    return _format_list()


def _todo_read(_arguments: dict[str, Any], _cwd: Any = None) -> str:
    if not _items:
        return "No TODO items yet."
    return _format_list()


def _format_list() -> str:
    if not _items:
        return "TODO list is empty."
    lines = []
    for item in _items:
        icon = _STATUS_ICON[item["status"]] if item["status"] in _STATUS_ICON else "?"
        lines.append(f"{icon} {item['text']}")
    return "\n".join(lines)


_STATUS_ICON = {
    "pending": "⬜",
    "in_progress": "🔵",
    "done": "✅",
    "cancelled": "❌",
}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Replace the full TODO list. Call this after the user approves your plan "
                "to create the initial list, then again to update item statuses as you work "
                "(pending → in_progress → done)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The complete new TODO list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Stable identifier, e.g. '1', '2a'."},
                                "text": {"type": "string", "description": "Short description of the task."},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "done", "cancelled"],
                                    "description": "Current status.",
                                },
                            },
                            "required": ["id", "text", "status"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Return the current TODO list.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

HANDLERS: dict[str, Any] = {
    "todo_write": _todo_write,
    "todo_read": _todo_read,
}
