from importlib.metadata import version

import typer

app = typer.Typer(help="A Python CLI coding agent powered by OpenRouter.")

__version__ = version("my-coding-agent")


def version_callback(show: bool) -> None:
    if show:
        typer.echo(f"agent {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(  # noqa: ARG001
        False,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    prompt: str = typer.Option(None, "--prompt", "-p", help="Single-shot prompt; exits after one turn."),
    model: str = typer.Option(
        "deepseek/deepseek-v4.1-flash",
        "--model",
        "-m",
        help="OpenRouter model ID.",
    ),
    no_approval: bool = typer.Option(False, "--no-approval", help="Bypass all approval prompts."),
    no_plan: bool = typer.Option(False, "--no-plan", help="Disable plan mode."),
    web: bool = typer.Option(False, "--web", help="Enable web search and fetch tools."),
    base_url: str = typer.Option(
        "https://openrouter.ai/api/v1",
        "--base-url",
        help="OpenAI-compatible API base URL.",
    ),
) -> None:
    """Start the coding agent REPL or run a single prompt."""
    if ctx.invoked_subcommand is not None:
        return

    typer.echo(f"agent {__version__}  model={model}")
    if prompt:
        typer.echo(f"(single-shot) {prompt}")
    else:
        typer.echo("REPL not yet implemented. Use --prompt / -p for now.")
