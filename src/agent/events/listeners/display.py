"""Display listener — renders all agent output to the terminal via Rich."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from agent.events.bus import Event, EventBus

console = Console()

# Tools whose output should be shown as a diff
_DIFF_TOOLS = {"write_file", "edit_file"}
# Tools that are read-only — show output collapsed
_QUIET_TOOLS = {"read_file", "find_files", "grep_files"}
# TODO status icons
_TODO_ICON = {
    "pending": "⬜",
    "in_progress": "🔵",
    "done": "✅",
    "cancelled": "❌",
}


def _tool_icon(name: str) -> str:
    if name.startswith("mcp__git__"):
        return "⬡"
    if name in ("bash",):
        return "$"
    if name in ("web_search", "web_fetch"):
        return "🌐"
    return "⚙"


class DisplayListener:
    """Subscribes to bus events and renders them to the terminal."""

    def __init__(self) -> None:
        self._streaming_text = ""
        self._current_tool: str | None = None
        self._tool_input: dict | None = None

    def register(self, bus: "EventBus") -> None:
        from agent.events.types import (
            CONTEXT_COMPACT,
            ERROR,
            MCP_SERVER_START,
            MESSAGE_ASSISTANT,
            STREAM_DELTA,
            SUBAGENT_END,
            SUBAGENT_START,
            TOOL_AFTER,
            TOOL_BEFORE,
            TURN_END,
        )
        bus.on(STREAM_DELTA, self._on_delta)
        bus.on(MESSAGE_ASSISTANT, self._on_message_done)
        bus.on(TOOL_BEFORE, self._on_tool_before)
        bus.on(TOOL_AFTER, self._on_tool_after)
        bus.on(TURN_END, self._on_turn_end)
        bus.on(CONTEXT_COMPACT, self._on_context_compact)
        bus.on(SUBAGENT_START, self._on_subagent_start)
        bus.on(SUBAGENT_END, self._on_subagent_end)
        bus.on(MCP_SERVER_START, self._on_mcp_start)
        bus.on(ERROR, self._on_error)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_delta(self, event: "Event") -> None:
        text = event.data["text"]
        self._streaming_text += text
        console.print(text, end="", markup=False, highlight=False)

    def _on_message_done(self, event: "Event") -> None:
        if self._streaming_text:
            console.print()  # newline after streamed text
            self._streaming_text = ""

    def _on_tool_before(self, event: "Event") -> None:
        if self._streaming_text:
            console.print()
            self._streaming_text = ""
        self._current_tool = event.data["tool"]
        self._tool_input = event.data.get("input", {})

        # Silent tools — their TOOL_AFTER output is what matters
        if self._current_tool in ("todo_write", "todo_read", "remember", "forget"):
            return

        icon = _tool_icon(self._current_tool)
        label = f"{icon} [bold]{self._current_tool}[/bold]"
        if self._current_tool == "bash":
            cmd = (self._tool_input or {}).get("command", "")
            label += f"  [dim]{cmd[:80]}[/dim]"
        elif self._current_tool in ("read_file", "write_file", "edit_file"):
            path = (self._tool_input or {}).get("path", "")
            label += f"  [dim]{path}[/dim]"
        console.print(f"\n[dim]{label}[/dim]")

    def _on_tool_after(self, event: "Event") -> None:
        tool = event.data["tool"]
        output: str = event.data.get("output", "")
        duration = event.data.get("duration_ms", 0)

        # Strip injection wrapper for display
        import re
        inner = re.sub(r"<tool_result[^>]*>(.*?)</tool_result>", r"\1", output, flags=re.DOTALL).strip()

        if tool == "todo_write":
            self._show_todo_list(inner)
            return  # skip duration line — todo updates are self-explanatory
        elif tool in ("remember", "forget"):
            console.print(f"[dim]💾 {inner}[/dim]")
            return
        elif tool in _DIFF_TOOLS:
            self._show_diff(tool, inner)
        elif tool in _QUIET_TOOLS:
            # Show first few lines only
            lines = inner.splitlines()
            preview = "\n".join(lines[:10])
            if len(lines) > 10:
                preview += f"\n[dim]… {len(lines) - 10} more lines[/dim]"
            console.print(Panel(preview, border_style="dim", padding=(0, 1)))
        elif inner and tool == "bash":
            lines = inner.splitlines()
            preview = "\n".join(lines[:20])
            if len(lines) > 20:
                preview += f"\n[dim]… {len(lines) - 20} more lines[/dim]"
            console.print(Panel(preview, border_style="dim", padding=(0, 1)))
        elif tool == "todo_read":
            pass  # suppress — model uses this for its own tracking

        console.print(f"[dim]   └ {duration}ms[/dim]")

    def _on_turn_end(self, event: "Event") -> None:
        usage = event.data.get("usage", {})
        if usage:
            inp = usage.get("prompt_tokens", 0)
            out = usage.get("completion_tokens", 0)
            cached = usage.get("cached_tokens", 0)
            parts = [f"{inp:,} in", f"{out:,} out"]
            if cached:
                parts.append(f"[green]{cached:,} cached[/green]")
            console.print(f"\n[dim]↳ {' / '.join(parts)} tokens[/dim]")

    def _on_subagent_start(self, event: "Event") -> None:
        sid = event.data.get("subagent_id", "?")
        prompt = event.data.get("prompt", "")[:60]
        console.print(f"\n[dim]  ↳ subagent [{sid}] {prompt}…[/dim]")

    def _on_subagent_end(self, event: "Event") -> None:
        sid = event.data.get("subagent_id", "?")
        console.print(f"[dim]  ↳ subagent [{sid}] done[/dim]")

    def _on_context_compact(self, event: "Event") -> None:
        tokens_before = event.data.get("tokens_before", 0)
        msgs_before = event.data.get("msgs_before", 0)
        msgs_after = event.data.get("msgs_after", 0)
        token_part = f"  [{tokens_before:,} tokens freed]" if tokens_before else ""
        console.print(
            f"\n[dim cyan]⚡ Context compacted: {msgs_before} → {msgs_after} messages{token_part}[/dim cyan]"
        )

    def _on_mcp_start(self, event: "Event") -> None:
        name = event.data["server"]
        count = event.data["tools"]
        console.print(f"[dim]⬡ MCP [{name}] {count} tools[/dim]")

    def _on_error(self, event: "Event") -> None:
        console.print(f"\n[red bold]Error:[/red bold] {event.data.get('error', event.data)}")

    # ------------------------------------------------------------------
    # TODO list rendering
    # ------------------------------------------------------------------

    def _show_todo_list(self, text: str) -> None:
        from agent.tools.todo import get_todos
        items = get_todos()
        if not items:
            return

        done = sum(1 for i in items if i["status"] == "done")
        total = len(items)
        progress = f"{done}/{total}"

        lines = []
        for item in items:
            icon = _TODO_ICON.get(item["status"], "⬜")
            if item["status"] == "done":
                lines.append(f"  {icon}  [dim]{item['text']}[/dim]")
            elif item["status"] == "in_progress":
                lines.append(f"  {icon}  [bold]{item['text']}[/bold]")
            elif item["status"] == "cancelled":
                lines.append(f"  {icon}  [dim red]{item['text']}[/dim red]")
            else:
                lines.append(f"  {icon}  {item['text']}")

        console.print(
            Panel(
                "\n".join(lines),
                title=f"[bold cyan]Tasks[/bold cyan]  [dim]{progress} done[/dim]",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    # ------------------------------------------------------------------
    # Diff rendering
    # ------------------------------------------------------------------

    def _show_diff(self, tool: str, output: str) -> None:
        if "Error:" in output:
            console.print(f"[red]{output}[/red]")
            return
        # For write_file we don't have the old content, just show a success note
        if tool == "write_file":
            console.print(f"[green]{output}[/green]")
            return
        # For edit_file show a simple success
        console.print(f"[green]{output}[/green]")
