"""CLI entry point for the coding agent."""

import asyncio
import signal
from importlib.metadata import version
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    help="A Python CLI coding agent.",
    add_completion=False,
)

__version__ = version("my-coding-agent")


def _version_callback(show: bool) -> None:
    if show:
        typer.echo(f"agent {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    # Core options
    prompt: Optional[str] = typer.Option(None, "--prompt", "-p", help="Run a single prompt and exit."),
    resume: Optional[str] = typer.Option(None, "--resume", "-r", help="Resume a previous session by ID."),
    # Model / behaviour
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to use (overrides settings)."),
    system_file: Optional[str] = typer.Option(None, "--system-file", help="Path to a custom system prompt file."),
    # Permission modes
    no_approval: bool = typer.Option(False, "--no-approval", help="Auto-approve all tool calls (bypassPermissions mode)."),
    accept_edits: bool = typer.Option(False, "--accept-edits", help="Auto-approve file edits but prompt for bash (acceptEdits mode)."),
    # Feature flags
    web: bool = typer.Option(False, "--web", help="Enable web search and web fetch tools."),
    no_plan: bool = typer.Option(False, "--no-plan", help="Disable plan mode (no approval gate before execution)."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable auto-memory extraction at session end."),
    # Sessions
    list_sessions: bool = typer.Option(False, "--list-sessions", "-l", help="List recent sessions and exit."),
    # Webhooks
    webhook_url: Optional[str] = typer.Option(None, "--webhook-url", help="URL to POST events to."),
    webhook_events: Optional[str] = typer.Option(
        None, "--webhook-events",
        help="Comma-separated list of event types to POST (default: all)."
    ),
    # Version
    version: bool = typer.Option(
        False, "--version", "-V",
        callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Start an interactive coding session.

    Configuration lives in .agent/settings.json or ~/.agent/settings.json.
    Secrets (API key) go in .env. Use slash commands inside the session.

    Examples:

    \b
      uv run agent                          # interactive REPL
      uv run agent -p "explain this code"  # single-shot mode
      uv run agent --resume abc12345       # resume a previous session
      uv run agent --web -p "latest uv release?"
    """
    if ctx.invoked_subcommand is not None:
        return

    from rich.console import Console
    from agent.config.config import OPENROUTER_API_KEY, load_config

    console = Console()

    # --list-sessions shortcut
    if list_sessions:
        from agent.events.listeners.persistence import list_sessions as ls
        import datetime
        sessions = ls(Path.cwd())
        if not sessions:
            console.print("[dim]No saved sessions.[/dim]")
        else:
            for s in sessions:
                ts = datetime.datetime.fromtimestamp(s["saved_at"]).strftime("%Y-%m-%d %H:%M")
                console.print(f"  [cyan]{s['id']}[/cyan]  {ts}  {s['turns']} turns  [dim]{s['first_message'] or ''}[/dim]")
        raise typer.Exit()

    if not OPENROUTER_API_KEY:
        console.print(
            "[red]Error:[/red] OPENROUTER_API_KEY is not set.\n"
            "Add it to a [bold].env[/bold] file in this directory:\n\n"
            "  OPENROUTER_API_KEY=sk-or-..."
        )
        raise typer.Exit(1)

    # Build CLI overrides (None values are skipped by load_config)
    overrides: dict = {}
    if model:
        overrides["model"] = model
    if system_file:
        overrides["system_file"] = system_file
    if web:
        overrides["web_search"] = True
    if no_plan:
        overrides["plan_mode"] = False
    if no_memory:
        overrides["auto_memory"] = False
    if no_approval:
        overrides["approval_mode"] = "bypassPermissions"
    elif accept_edits:
        overrides["approval_mode"] = "acceptEdits"
    if webhook_url:
        overrides["webhook_url"] = webhook_url
    if webhook_events:
        overrides["webhook_events"] = [e.strip() for e in webhook_events.split(",") if e.strip()]

    cfg = load_config(cli_overrides=overrides)

    console.print(f"[dim]agent {__version__}  model=[bold]{cfg.model}[/bold][/dim]\n")

    # Support piped input: if stdin is not a tty, read it and prepend to prompt
    import sys
    if not sys.stdin.isatty() and not prompt:
        piped = sys.stdin.read().strip()
        if piped:
            prompt = piped

    from agent.repl import run_repl
    asyncio.run(run_repl(cfg, initial_prompt=prompt, resume_id=resume or None))
