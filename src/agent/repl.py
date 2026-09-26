"""Interactive REPL for the coding agent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt

if TYPE_CHECKING:
    from agent.config.config import Config

console = Console()

HELP_TEXT = """\
[bold]Slash commands[/bold]
  /help     — show this message
  /clear    — clear conversation history
  /cost     — show token usage (coming soon)
  /compact  — summarise history (coming soon)
  /exit     — end the session

[dim]Ctrl-C cancels the current response. Ctrl-D exits.[/dim]
"""


def _build_initial_messages(cwd: Path, cfg: "Config") -> list[dict]:
    from agent.memory.loader import assemble_system_prompt

    blocks = assemble_system_prompt(cwd, cfg)

    # Use content block list when blocks have cache_control (OpenRouter/Claude),
    # otherwise collapse to a plain string for full compatibility.
    has_cache = any("cache_control" in b for b in blocks)
    content = blocks if has_cache else "\n\n".join(b["text"] for b in blocks)

    return [{"role": "system", "content": content}]


async def run_repl(cfg: "Config", initial_prompt: str | None = None) -> None:
    from agent.loop import make_client, stream_turn

    cwd = Path.cwd()
    client = make_client(cfg)
    messages = _build_initial_messages(cwd, cfg)

    async def handle_turn(user_input: str) -> None:
        messages.append({"role": "user", "content": user_input})
        console.print()
        try:
            full = ""
            async for chunk in stream_turn(client, messages, cfg):
                console.print(chunk, end="", markup=False, highlight=False)
                full += chunk
            console.print()  # newline after streamed output
        except KeyboardInterrupt:
            # Remove the incomplete assistant message if added
            if messages and messages[-1]["role"] == "assistant":
                messages.pop()
            console.print("\n[dim]Cancelled.[/dim]")
        except Exception as e:
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            console.print(f"\n[red]Error:[/red] {e}")

    # Single-shot mode
    if initial_prompt:
        await handle_turn(initial_prompt)
        return

    # Interactive REPL
    console.print("[dim]Type /help for commands, Ctrl-D to exit.[/dim]\n")
    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: Prompt.ask("[bold cyan]you[/bold cyan]")
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Slash commands
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
            else:
                console.print(f"[yellow]Unknown command:[/yellow] {cmd}  (try /help)")
            continue

        await handle_turn(user_input)
