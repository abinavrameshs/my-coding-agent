"""Interactive REPL for the coding agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from agent.config.config import Config

console = Console()

HELP_TEXT = """\
[bold]Slash commands[/bold]
  /help     — show this message
  /clear    — clear conversation history
  /tools    — list available tools
  /cost     — show token usage (coming soon)
  /compact  — summarise history (coming soon)
  /mcp      — list MCP servers (coming soon)
  /exit     — end the session

[dim]Ctrl-C cancels the current response. Ctrl-D exits.[/dim]
"""


def _build_initial_messages(cwd: Path, cfg: "Config") -> list[dict]:
    from agent.memory.loader import assemble_system_prompt

    blocks = assemble_system_prompt(cwd, cfg)
    has_cache = any("cache_control" in b for b in blocks)
    content = blocks if has_cache else "\n\n".join(b["text"] for b in blocks)
    return [{"role": "system", "content": content}]


async def run_repl(cfg: "Config", initial_prompt: str | None = None) -> None:
    from agent.events.bus import Event, EventBus
    from agent.events.types import SESSION_END, SESSION_START, STREAM_DELTA, TOOL_AFTER, TOOL_BEFORE, ERROR
    from agent.loop import make_client, run_turn
    from agent.mcp.manager import MCPManager
    from agent.tools.registry import ToolRegistry

    cwd = Path.cwd()
    bus = EventBus()

    # --- Display listener ---
    def on_stream_delta(e: Event) -> None:
        console.print(e.data["text"], end="", markup=False, highlight=False)

    async def on_tool_before(e: Event) -> None:
        console.print(f"\n[dim]⚙ {e.data['tool']}[/dim] ", end="")

    async def on_tool_after(e: Event) -> None:
        console.print(f"[dim]({e.data['duration_ms']}ms)[/dim]")

    def on_error(e: Event) -> None:
        console.print(f"\n[red]Error:[/red] {e.data.get('error', e.data)}")

    bus.on(STREAM_DELTA, on_stream_delta)
    bus.on(TOOL_BEFORE, on_tool_before)
    bus.on(TOOL_AFTER, on_tool_after)
    bus.on(ERROR, on_error)

    # --- Start MCP servers ---
    mcp = MCPManager()
    try:
        await mcp.start_all(cfg, bus, cwd)
    except Exception as e:
        console.print(f"[yellow]MCP startup warning:[/yellow] {e}")

    client = make_client(cfg)
    registry = ToolRegistry(cfg, cwd, mcp)
    messages = _build_initial_messages(cwd, cfg)

    await bus.emit(Event(SESSION_START, {"model": cfg.model}))

    async def handle_turn(user_input: str) -> None:
        messages.append({"role": "user", "content": user_input})
        console.print()
        try:
            await run_turn(client, messages, cfg, bus, registry)
            console.print()
        except KeyboardInterrupt:
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            console.print("\n[dim]Cancelled.[/dim]")
        except Exception as e:
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            console.print(f"\n[red]Error:[/red] {e}")

    # Single-shot mode
    if initial_prompt:
        await handle_turn(initial_prompt)
        await mcp.stop_all()
        await bus.emit(Event(SESSION_END, {}))
        return

    # Interactive REPL
    console.print("[dim]Type /help for commands, Ctrl-D to exit.[/dim]\n")
    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: console.input("[bold cyan]you>[/bold cyan] ")
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.split()[0].lower()
            if cmd in ("/exit", "/quit"):
                console.print("[dim]Goodbye.[/dim]")
                break
            elif cmd == "/help":
                console.print(HELP_TEXT)
            elif cmd == "/clear":
                messages[:] = _build_initial_messages(cwd, cfg)
                console.print("[dim]History cleared.[/dim]")
            elif cmd == "/tools":
                for t in registry.schemas():
                    name = t["function"]["name"]
                    desc = t["function"].get("description", "")[:60]
                    console.print(f"  [cyan]{name}[/cyan]  {desc}")
            else:
                console.print(f"[yellow]Unknown command:[/yellow] {cmd}  (try /help)")
            continue

        await handle_turn(user_input)

    await mcp.stop_all()
    await bus.emit(Event(SESSION_END, {}))
