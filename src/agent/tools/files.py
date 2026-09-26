"""Native file tools: read, write, edit, grep, find."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


class ToolError(Exception):
    pass


def _safe_path(raw: str, cwd: Path) -> Path:
    """Resolve *raw* relative to *cwd* and reject path-traversal attempts."""
    path = (cwd / raw).resolve()
    if not str(path).startswith(str(cwd.resolve())):
        raise ToolError(f"Path '{raw}' escapes the working directory")
    return path


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def read_file(arguments: dict[str, Any], cwd: Path) -> str:
    path = _safe_path(arguments["path"], cwd)
    if not path.exists():
        raise ToolError(f"File not found: {arguments['path']}")
    lines = path.read_text(errors="replace").splitlines()
    start = max(0, arguments.get("start_line", 1) - 1)
    end = arguments.get("end_line")
    if end is not None:
        lines = lines[start:end]
    else:
        lines = lines[start:]
    return "\n".join(f"{start + i + 1:4d} | {line}" for i, line in enumerate(lines))


def write_file(arguments: dict[str, Any], cwd: Path) -> str:
    path = _safe_path(arguments["path"], cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = arguments["content"]
    path.write_text(content)
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return f"Wrote {line_count} lines to {arguments['path']}"


def edit_file(arguments: dict[str, Any], cwd: Path) -> str:
    path = _safe_path(arguments["path"], cwd)
    if not path.exists():
        raise ToolError(f"File not found: {arguments['path']}")
    old = arguments["old_string"]
    new = arguments["new_string"]
    content = path.read_text()
    count = content.count(old)
    if count == 0:
        raise ToolError("old_string not found in file")
    if count > 1:
        raise ToolError(f"old_string found {count} times — must be unique; add more context")
    path.write_text(content.replace(old, new, 1))
    return f"Edited {arguments['path']}"


def grep_files(arguments: dict[str, Any], cwd: Path) -> str:
    pattern = arguments["pattern"]
    include = arguments.get("include", "*")
    max_results = arguments.get("max_results", 50)

    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", include, pattern, str(cwd)],
            capture_output=True, text=True, timeout=15,
        )
        lines = result.stdout.splitlines()
    except FileNotFoundError:
        # grep not available — fall back to Python
        lines = []
        rx = re.compile(pattern)
        for p in cwd.rglob(include.replace("*", "**/*") if "." in include else "*"):
            if not p.is_file():
                continue
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        lines.append(f"{p}:{i}: {line}")
            except Exception:
                pass

    if not lines:
        return "No matches found"

    clipped = lines[:max_results]
    result_text = "\n".join(l.replace(str(cwd) + "/", "") for l in clipped)
    if len(lines) > max_results:
        result_text += f"\n... ({len(lines) - max_results} more matches)"
    return result_text


def find_files(arguments: dict[str, Any], cwd: Path) -> str:
    pattern = arguments["pattern"]
    matches = [
        str(p.relative_to(cwd))
        for p in cwd.rglob(pattern)
        if not any(part.startswith(".") for part in p.parts)
    ]
    if not matches:
        return "No files found"
    return "\n".join(sorted(matches))


# ---------------------------------------------------------------------------
# OpenAI tool schemas
# ---------------------------------------------------------------------------

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents with line numbers. Optionally specify a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to cwd"},
                    "start_line": {"type": "integer", "description": "First line to read (1-indexed)"},
                    "end_line": {"type": "integer", "description": "Last line to read (inclusive)"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating it or overwriting it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace exactly one occurrence of old_string with new_string in a file. "
                "old_string must be unique in the file — add surrounding lines if needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "Search for a regex pattern across files. Returns file:line:content matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "include": {"type": "string", "description": "Glob to filter files, e.g. '*.py'"},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files matching a glob pattern (hidden directories excluded).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
]

HANDLERS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "grep_files": grep_files,
    "find_files": find_files,
}
