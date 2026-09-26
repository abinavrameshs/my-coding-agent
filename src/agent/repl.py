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
  /help              — show this message
  /clear             — clear conversation history
  /tools             — list active tools and available groups
  /tools on <group>  — enable a tool group (git, web, mcp)
  /tools off <group> — disable a tool group
  /cost              — show token usage (coming soon)
  /compact           — summarise history (coming soon)
  /exit              — end the session
"""


def _build_initial_messages(cwd: Path, cfg: "Config") -> list[dict]:
    from agent.memory.loader import assemble_system_prompt
    blocks = assemble_system_prompt(cwd, cfg)
    has_cache = any("cache_control" in b for b in blocks)
    content = blocks if has_cache else "\n\n".join(b["text"] for b in blocks)
    return [{"role": "system", "content": content}]


async def run_repl(cfg: "Config", initial_prompt: str | None = None) -> None:
    from agent.events.bus import Event, EventBus
    from agent.events.listeners.approval import ApprovalListener
    from agent.events.listeners.display import DisplayListener
    from agent.events.types import SESSION_END, SESSION_START
    from agent.loop import make_client, run_turn
    from agent.mcp.manager import MCPManager
    from agent.tools.registry import ToolRegistry

    cwd = Path.cwd()
    bus = EventBus()

    # Register listeners
    DisplayListener().register(bus)
    ApprovalListener(cfg).register(bus)

    # Start MCP servers
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

    # Show active tool groups on start
    counts = registry.tool_count_by_group()
    active = registry.active_groups()
    group_summary = ", ".join(
        f"{g.value}({counts.get(g.value, '?')})" for g in active
    )
    console.print(f"[dim]Tools: {group_summary}  — /tools to manage[/dim]\n")

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
                from agent.tools.registry import ToolGroup
                parts = user_input.split()
                if len(parts) == 3 and parts[1] in ("on", "off"):
                    action, group_name = parts[1], parts[2].lower()
                    try:
                        g = ToolGroup(group_name)
                        if action == "on":
                            registry.enable(g)
                            console.print(f"[green]Enabled[/green] {group_name} tools")
                        else:
                            registry.disable(g)
                            console.print(f"[yellow]Disabled[/yellow] {group_name} tools")
                    except ValueError:
                        console.print(f"[red]Unknown group:[/red] {group_name}  (core, git, web, mcp)")
                else:
                    # List active tools and available groups
                    active = registry.active_groups()
                    counts = registry.tool_count_by_group()
                    console.print(f"\n[bold]Active groups:[/bold]")
                    for g in ToolGroup:
                        status = "[green]on [/green]" if g in active else "[dim]off[/dim]"
                        count = counts.get(g.value, 0)
                        console.print(f"  {status} [cyan]{g.value}[/cyan] ({count} tools)")
                    console.print(f"\n[bold]Active tools ({len(registry.schemas())}):[/bold]")
                    for t in registry.schemas():
                        name = t["function"]["name"]
                        desc = t["function"].get("description", "")[:55]
                        console.print(f"  [cyan]{name}[/cyan]  [dim]{desc}[/dim]")
            else:
                console.print(f"[yellow]Unknown command:[/yellow] {cmd}  (try /help)")
            continue

        await handle_turn(user_input)

    await mcp.stop_all()
    await bus.emit(Event(SESSION_END, {}))
