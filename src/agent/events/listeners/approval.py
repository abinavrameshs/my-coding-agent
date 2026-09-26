"""Approval listener — gates destructive tool calls based on permission mode.

Permission modes (set in config or .agent/settings.json):
  default          — auto-approve reads; prompt for writes and bash
  acceptEdits      — auto-approve reads + writes; prompt for bash
  auto             — auto-approve everything except dangerous bash patterns
  bypassPermissions — approve everything silently
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.events.bus import Event, EventBus

console = Console()

_READ_TOOLS = {"read_file", "find_files", "grep_files", "web_search", "web_fetch"}
_WRITE_TOOLS = {"write_file", "edit_file"}
_DANGEROUS_BASH = ["rm -rf", "sudo ", "curl | sh", "wget | sh", "> /etc/", "dd if=", "mkfs"]

# Tracks "approve all remaining writes this turn" state per session
_approve_all: bool = False


def _is_dangerous_bash(command: str) -> bool:
    return any(p in command for p in _DANGEROUS_BASH)


class ApprovalListener:
    def __init__(self, cfg: "Config") -> None:
        self._cfg = cfg
        self._approve_all_writes = False

    def register(self, bus: "EventBus") -> None:
        from agent.events.types import SESSION_START, TOOL_BEFORE
        bus.on(TOOL_BEFORE, self._on_tool_before)
        bus.on(SESSION_START, lambda _: self._reset())

    def _reset(self) -> None:
        self._approve_all_writes = False

    def _on_tool_before(self, event: "Event") -> None:
        tool = event.data["tool"]
        inp = event.data.get("input", {})
        mode = self._cfg.approval_mode

        # Allowed tools list overrides everything
        if self._cfg.allowed_tools and tool in self._cfg.allowed_tools:
            return
        if tool in self._cfg.disallowed_tools:
            event.data["cancelled"] = True
            console.print(f"[yellow]Blocked:[/yellow] {tool} is in disallowed_tools")
            return

        # bypassPermissions — approve everything
        if mode == "bypassPermissions":
            return

        # Read-only tools — always approved in all modes
        if tool in _READ_TOOLS or tool.startswith("mcp__git__git_log") or tool.startswith("mcp__git__git_diff"):
            return

        # auto mode — use JEV to decide bash safety, block only when JEV says risky
        if mode == "auto":
            if tool == "bash":
                import asyncio

                from agent.routing import should_approve_bash
                try:
                    safe = asyncio.get_event_loop().run_until_complete(
                        asyncio.wait_for(should_approve_bash(inp.get("command", ""), self._cfg), timeout=6.0)
                    )
                except Exception:
                    safe = not _is_dangerous_bash(inp.get("command", ""))  # fallback
                if not safe:
                    if not self._prompt(tool, inp, force=True):
                        event.data["cancelled"] = True
            return

        # acceptEdits mode — auto-approve writes, prompt for bash
        if mode == "acceptEdits":
            if tool in _WRITE_TOOLS or tool.startswith("mcp__git__"):
                return
            if tool == "bash":
                if not self._prompt(tool, inp):
                    event.data["cancelled"] = True
            return

        # default mode — prompt for writes and bash
        if tool in _WRITE_TOOLS:
            if self._approve_all_writes:
                return
            if not self._prompt(tool, inp):
                event.data["cancelled"] = True
        elif tool == "bash" or tool.startswith("mcp__git__git_commit") or tool.startswith("mcp__git__git_push"):
            if not self._prompt(tool, inp):
                event.data["cancelled"] = True

    def _prompt(self, tool: str, inp: dict, force: bool = False) -> bool:
        """Ask the user to approve. Returns True to proceed, False to cancel."""
        label = tool
        if tool == "bash":
            label = f"bash: {inp.get('command', '')[:80]}"
        elif tool in _WRITE_TOOLS:
            label = f"{tool}: {inp.get('path', '')}"

        suffix = "" if force else " [dim](a=approve all writes, q=cancel turn)[/dim]"
        console.print(f"\n[yellow]Allow:[/yellow] {label}{suffix}")

        try:
            answer = input("  [y/N/a/q] → ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer in ("a", "all") and not force:
            self._approve_all_writes = True
            return True
        if answer in ("q", "quit"):
            return False
        return answer in ("y", "yes")
