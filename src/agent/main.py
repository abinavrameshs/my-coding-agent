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

    Configuration (model, approval mode, MCP servers, etc.) lives in
    .agent/settings.json or ~/.agent/settings.json. Secrets go in .env.
    Use slash commands inside the session to change behaviour on the fly.
    """
    if ctx.invoked_subcommand is not None:
        return

    from agent.config.config import load_config
    cfg = load_config()

    typer.echo(f"agent {__version__}  ({cfg.model})")

    if prompt:
        typer.echo(f"[single-shot] {prompt}")
        # TODO(Task 9): run agent loop for one turn then exit
    elif resume:
        typer.echo(f"[resume] session {resume}")
        # TODO(Task 13): load session history then start REPL
    else:
        typer.echo("Starting session — type /help for commands, Ctrl-C to exit.")
        # TODO(Task 20): start REPL loop
