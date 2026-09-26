"""Bash tool with timeout and output capping."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent.tools.files import ToolError

_DANGEROUS_PATTERNS = [
    "rm -rf",
    "sudo ",
    "curl | sh",
    "curl|sh",
    "wget | sh",
    "wget|sh",
    "> /etc/",
    "dd if=",
    "mkfs",
    ":(){:|:&};:",   # fork bomb
]

DEFAULT_TIMEOUT = 30  # seconds


def run_bash(
    command: str,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT,
    max_output_chars: int = 10_000,
) -> str:
    warning = ""
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in command:
            warning = f"[warning: command matches dangerous pattern '{pattern}']\n"
            break

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out after {timeout}s: {command!r}")

    if not output:
        output = "(no output)"

    if len(output) > max_output_chars:
        original_len = len(output)
        output = output[:max_output_chars]
        output += f"\n... [truncated: showed {max_output_chars} of {original_len} chars]"

    return warning + output


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Run a shell command and return combined stdout + stderr. "
            "Use for build commands, tests, git operations via CLI, and system tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in seconds (default {DEFAULT_TIMEOUT})",
                    "default": DEFAULT_TIMEOUT,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}
