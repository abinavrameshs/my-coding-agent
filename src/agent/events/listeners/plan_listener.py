"""PlanListener — shows the proposed plan and waits for user approval.

Handles the PLAN_PROPOSED event emitted by the loop when the model outputs
a <plan>...</plan> block before tool calls.

The user sees a Rich panel with the plan text and a prompt:
  [Enter = approve / e = edit in $EDITOR / q = cancel]

If the user cancels, event.data.cancelled is set to True so the loop
skips all tool execution and returns to the REPL.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from agent.events.bus import Event, EventBus

console = Console()


class PlanListener:
    def register(self, bus: "EventBus") -> None:
        from agent.events.types import PLAN_PROPOSED, SESSION_START
        bus.on(PLAN_PROPOSED, self._on_plan_proposed)
        bus.on(SESSION_START, lambda _: None)  # no-op — future hook

    def _on_plan_proposed(self, event: "Event") -> None:
        plan_text: str = event.data.plan

        console.print(
            Panel(
                plan_text,
                title="[bold cyan]Proposed plan[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        console.print("[dim]  Enter = approve  /  e = edit  /  q = cancel[/dim]")

        while True:
            try:
                answer = input("  → ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                event.data.cancelled = True
                console.print("\n[dim]Cancelled.[/dim]")
                return

            if answer in ("", "y", "yes"):
                console.print("[green]Plan approved — proceeding.[/green]\n")
                return
            elif answer in ("e", "edit"):
                edited = _open_in_editor(plan_text)
                if edited and edited.strip():
                    event.data.updated_plan = edited.strip()
                    console.print(
                        Panel(
                            edited.strip(),
                            title="[bold cyan]Updated plan[/bold cyan]",
                            border_style="cyan",
                            padding=(1, 2),
                        )
                    )
                    console.print("[green]Plan approved — proceeding.[/green]\n")
                    return
                else:
                    console.print("[yellow]No changes made.[/yellow]")
            elif answer in ("q", "quit", "n", "no"):
                event.data.cancelled = True
                console.print("[dim]Plan cancelled. Ask me to try a different approach.[/dim]\n")
                return
            else:
                console.print("[dim]  Enter = approve  /  e = edit  /  q = cancel[/dim]")


def _open_in_editor(text: str) -> str | None:
    """Open *text* in $EDITOR and return the saved content (or None on failure)."""
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            fname = f.name
        subprocess.run([editor, fname], check=False)
        with open(fname, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None
