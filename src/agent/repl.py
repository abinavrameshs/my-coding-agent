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
  /help                    — show this message
  /clear                   — clear conversation history
  /plan [task]             — show current TODO list, or ask agent to plan a task
  /tools                   — list active tools and available groups
  /tools on <group>        — enable a tool group (git, web, mcp)
  /tools off <group>       — disable a tool group
  /sessions                — list recent sessions
  /memory                  — show remembered facts for this project
  /memory delete <key>     — remove a specific memory
  /memory clear            — clear all project memories
  /compact                 — summarise conversation history to free context space
  /mcp                     — list connected MCP servers and their tools
  /mcp add <name> <cmd>    — add an MCP server for this session
  /skills                  — list available skills
  /init                    — generate an AGENT.md for this project
  /<skill-name>            — invoke a skill from .agent/skills/
  /exit                    — end the session
"""


def _build_initial_messages(cwd: Path, cfg: "Config") -> list[dict]:
    from agent.memory.loader import assemble_system_prompt
    blocks = assemble_system_prompt(cwd, cfg)
    has_cache = any("cache_control" in b for b in blocks)
    content = blocks if has_cache else "\n\n".join(b["text"] for b in blocks)
    return [{"role": "system", "content": content}]


async def run_repl(
    cfg: "Config",
    initial_prompt: str | None = None,
    resume_id: str | None = None,
) -> None:
    from agent.events.bus import Event, EventBus
    from agent.events.listeners.approval import ApprovalListener
    from agent.events.listeners.auto_memory import AutoMemoryListener
    from agent.events.listeners.cost_tracker import CostTrackerListener
    from agent.events.listeners.display import DisplayListener
    from agent.events.listeners.persistence import PersistenceListener, load_session
    from agent.events.listeners.plan_listener import PlanListener
    from agent.events.listeners.transcript import TranscriptListener
    from agent.events.listeners.webhook import WebhookListener
    from agent.events.types import SESSION_END, SESSION_START
    from agent.loop import make_client, run_turn
    from agent.mcp.manager import MCPManager
    from agent.session import new_session_id
    from agent.tools.registry import ToolRegistry
    from agent.tools.todo import reset_todos

    cwd = Path.cwd()
    bus = EventBus()
    session_id = resume_id or new_session_id()

    # Track latest token usage from TURN_END so we can auto-compact
    _last_usage: dict = {}

    def _capture_usage(event: "Event") -> None:
        _last_usage.update(event.data.get("usage", {}))

    bus.on("turn.end", _capture_usage)

    # Register listeners
    DisplayListener().register(bus)
    ApprovalListener(cfg).register(bus)
    CostTrackerListener().register(bus)
    TranscriptListener(session_id, cwd).register(bus)
    if cfg.plan_mode:
        PlanListener().register(bus)
    WebhookListener(cfg).register(bus)

    reset_todos()

    # Start MCP servers
    mcp = MCPManager()
    try:
        await mcp.start_all(cfg, bus, cwd)
    except Exception as e:
        console.print(f"[yellow]MCP startup warning:[/yellow] {e}")

    client = make_client(cfg)
    _base_registry = ToolRegistry(cfg, cwd, mcp)
    from agent.subagent import _SubagentRegistry
    registry = _SubagentRegistry(_base_registry, bus, bus, cfg, cwd, depth=0)

    # Load prior session or build fresh messages
    if resume_id:
        prior = load_session(resume_id, cwd)
        if prior:
            messages = prior
            console.print(f"[dim]Resumed session {resume_id} ({sum(1 for m in prior if m['role'] == 'user')} turns)[/dim]\n")
        else:
            console.print(f"[yellow]Session {resume_id} not found — starting fresh.[/yellow]\n")
            messages = _build_initial_messages(cwd, cfg)
    else:
        messages = _build_initial_messages(cwd, cfg)

    # Wire persistence and auto-memory after messages list is finalised
    PersistenceListener(session_id, messages, cwd).register(bus)
    AutoMemoryListener(cfg, cwd, messages).register(bus)

    await bus.emit(Event(SESSION_START, {"model": cfg.model, "session_id": session_id}))

    async def _do_compact() -> bool:
        """Summarise conversation and return True if compaction happened."""
        from agent.context import compact_messages
        from agent.events.bus import Event
        from agent.events.types import CONTEXT_COMPACT
        _, tokens_before, tokens_after = await compact_messages(messages, cfg, client)
        if tokens_before != tokens_after:
            await bus.emit(Event(CONTEXT_COMPACT, {
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
            }))
            return True
        return False

    async def handle_turn(user_input: str) -> None:
        # Run JEV checks in parallel: tool-group routing + memory intent detection
        from agent.routing import auto_enable_groups, has_memory_intent

        async def _check_memory_intent() -> None:
            try:
                is_memory = await asyncio.wait_for(
                    has_memory_intent(user_input, cfg),
                    timeout=cfg.jev_timeout,
                )
                if not is_memory:
                    return
                from agent.memory.auto import extract_from_message
                facts = await asyncio.wait_for(
                    extract_from_message(user_input, cfg),
                    timeout=15.0,
                )
                if facts:
                    from agent.tools.memory_tools import HANDLERS as MH
                    for key, value in facts.items():
                        MH["remember"]({"key": key, "value": value}, cwd)
                        console.print(f"[dim]💾 Remembered: {key} = {value}[/dim]")
            except Exception:
                pass  # memory intent detection is non-fatal

        try:
            await asyncio.gather(
                asyncio.wait_for(
                    auto_enable_groups(user_input, registry, cwd, cfg, console),
                    timeout=cfg.jev_timeout,
                ),
                _check_memory_intent(),
                return_exceptions=True,
            )
        except Exception:
            pass

        messages.append({"role": "user", "content": user_input})
        console.print()
        try:
            await run_turn(client, messages, cfg, bus, registry)
            console.print()
            # Auto-compact when prompt tokens exceed 80% of context_limit
            prompt_tokens = _last_usage.get("prompt_tokens", 0)
            if prompt_tokens > 0.8 * cfg.context_limit:
                await _do_compact()
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

    # Show session ID and active tool groups on start
    console.print(f"[dim]Session: {session_id}[/dim]")
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
            elif cmd == "/plan":
                # Show current TODO list, or prompt user for a task to plan
                from agent.tools.todo import get_todos
                items = get_todos()
                if items:
                    from agent.tools.todo import _STATUS_ICON
                    console.print("\n[bold]Current TODO list:[/bold]")
                    for item in items:
                        icon = _STATUS_ICON.get(item["status"], "?")
                        console.print(f"  {icon}  {item['text']}")
                    console.print()
                else:
                    parts = user_input.split(None, 1)
                    task = parts[1] if len(parts) > 1 else None
                    if task:
                        await handle_turn(f"Please create a plan for: {task}")
                    else:
                        console.print("[dim]Usage: /plan <task description>  or ask me directly[/dim]")
            elif cmd == "/compact":
                console.print("[dim]Compacting conversation history…[/dim]")
                compacted = await _do_compact()
                if not compacted:
                    console.print("[dim]Nothing to compact yet (fewer than 3 turns).[/dim]")
            elif cmd == "/sessions":
                from agent.events.listeners.persistence import list_sessions
                import datetime
                sessions = list_sessions(cwd)
                if not sessions:
                    console.print("[dim]No saved sessions found.[/dim]")
                else:
                    console.print("\n[bold]Recent sessions:[/bold]")
                    for s in sessions:
                        ts = datetime.datetime.fromtimestamp(s["saved_at"]).strftime("%Y-%m-%d %H:%M")
                        turns = s["turns"]
                        first = s["first_message"] or "(no user messages)"
                        console.print(
                            f"  [cyan]{s['id']}[/cyan]  {ts}  {turns} turns  [dim]{first}[/dim]"
                        )
                    console.print(f"\n[dim]Resume with: uv run agent --resume <id>[/dim]")
            elif cmd == "/memory":
                from agent.memory.auto import clear_memories, delete_memory, get_memories
                parts = user_input.split(None, 2)
                subcommand = parts[1] if len(parts) > 1 else "list"
                memories = get_memories(cwd)
                if subcommand == "list" or len(parts) == 1:
                    if not memories:
                        console.print("[dim]No remembered facts for this project yet.[/dim]")
                    else:
                        console.print(f"\n[bold]Remembered context[/bold] ({len(memories)} facts)\n")
                        for k, v in memories.items():
                            console.print(f"  [cyan]{k}[/cyan]: {v}")
                        console.print(f"\n[dim]/memory delete <key>  or  /memory clear[/dim]")
                elif subcommand == "delete":
                    key = parts[2] if len(parts) > 2 else ""
                    if not key:
                        console.print("[yellow]Usage: /memory delete <key>[/yellow]")
                    elif delete_memory(cwd, key):
                        console.print(f"[green]Deleted:[/green] {key}")
                    else:
                        console.print(f"[yellow]Key not found:[/yellow] {key}")
                elif subcommand == "clear":
                    clear_memories(cwd)
                    console.print("[dim]All memories cleared for this project.[/dim]")
                else:
                    console.print("[dim]Usage: /memory  /memory delete <key>  /memory clear[/dim]")
            elif cmd == "/mcp":
                parts = user_input.split(None, 3)
                subcommand = parts[1] if len(parts) > 1 else "list"
                if subcommand == "list":
                    mcp_tools = mcp.tool_list()
                    if not mcp_tools:
                        console.print("[dim]No MCP tools active. Start servers via settings.json mcpServers.[/dim]")
                    else:
                        from collections import Counter
                        server_counts: Counter = Counter()
                        for t in mcp_tools:
                            # mcp__<server>__<tool>
                            parts_name = t["function"]["name"].split("__", 2)
                            server_counts[parts_name[1] if len(parts_name) >= 2 else "?"] += 1
                        console.print("\n[bold]MCP servers:[/bold]")
                        for server, count in sorted(server_counts.items()):
                            console.print(f"  [cyan]{server}[/cyan]  ({count} tools)")
                        console.print(f"\n[bold]MCP tools ({len(mcp_tools)}):[/bold]")
                        for t in mcp_tools:
                            tname = t["function"]["name"]
                            tdesc = t["function"].get("description", "")[:55]
                            console.print(f"  [cyan]{tname}[/cyan]  [dim]{tdesc}[/dim]")
                elif subcommand == "add":
                    if len(parts) < 4:
                        console.print("[yellow]Usage: /mcp add <name> <command>[/yellow]")
                    else:
                        server_name = parts[2]
                        command_str = parts[3]
                        from agent.config.config import MCPServerConfig
                        server_cfg = MCPServerConfig(command=command_str.split())
                        try:
                            await mcp.start_server(server_name, server_cfg, cfg, bus, cwd)
                            console.print(f"[green]Started MCP server:[/green] {server_name}")
                        except Exception as e:
                            console.print(f"[red]Failed to start {server_name}:[/red] {e}")
                else:
                    console.print("[dim]Usage: /mcp list  or  /mcp add <name> <command>[/dim]")
            elif cmd == "/skills":
                from agent.memory.skills import BUILTIN_SKILLS, discover_skills
                all_skills = {**discover_skills(cwd), **BUILTIN_SKILLS}
                # Builtin wins on collision (listed last so project skills below)
                all_skills = {**BUILTIN_SKILLS, **discover_skills(cwd)}
                if not all_skills:
                    console.print("[dim]No skills found. Add SKILL.md files to .agent/skills/<name>/[/dim]")
                else:
                    console.print("\n[bold]Available skills:[/bold]")
                    for sname, skill in sorted(all_skills.items()):
                        desc = f"  [dim]{skill.description}[/dim]" if skill.description else ""
                        console.print(f"  [cyan]/{sname}[/cyan]{desc}")
                    console.print()
            else:
                # Try to dispatch as a skill invocation: /skill-name [extra args]
                skill_name = cmd.lstrip("/")
                from agent.memory.skills import BUILTIN_SKILLS, discover_skills
                all_skills = {**BUILTIN_SKILLS, **discover_skills(cwd)}
                if skill_name in all_skills:
                    skill = all_skills[skill_name]
                    rendered = skill.render(cwd)
                    # Append any extra words after the command name as context
                    extra = user_input[len(cmd):].strip()
                    prompt = f"{rendered}\n\n{extra}" if extra else rendered
                    await handle_turn(prompt)
                else:
                    console.print(f"[yellow]Unknown command:[/yellow] {cmd}  (try /help or /skills)")
            continue

        await handle_turn(user_input)

    await mcp.stop_all()
    await bus.emit(Event(SESSION_END, {}))
