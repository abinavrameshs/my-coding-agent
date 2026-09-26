import asyncio
from importlib.metadata import version

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
    prompt: str = typer.Option(None, "--prompt", "-p", help="Run a single prompt and exit."),
    resume: str = typer.Option(None, "--resume", "-r", help="Resume a previous session by ID."),
    version: bool = typer.Option(  # noqa: ARG001
        False, "--version", "-V",
        callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Start an interactive coding session.

    Configuration lives in .agent/settings.json or ~/.agent/settings.json.
    Secrets (API key) go in .env. Use slash commands inside the session.
    """
    if ctx.invoked_subcommand is not None:
        return

    from agent.config.config import OPENROUTER_API_KEY, load_config
    from rich.console import Console

    console = Console()
    cfg = load_config()

    if not OPENROUTER_API_KEY:
        console.print(
            "[red]Error:[/red] OPENROUTER_API_KEY is not set.\n"
            "Add it to a [bold].env[/bold] file in this directory:\n\n"
            "  OPENROUTER_API_KEY=sk-or-..."
        )
        raise typer.Exit(1)

    console.print(f"[dim]agent {__version__}  model=[bold]{cfg.model}[/bold][/dim]\n")

    from agent.repl import run_repl

    asyncio.run(run_repl(cfg, initial_prompt=prompt, resume_id=resume or None))
