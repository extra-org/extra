"""CLI entrypoint for the declarative AI-agent platform.

The validation command is implemented for the YAML specification layer. Runtime
commands (``graph``, ``run``, ``serve``, ``deploy``) are intentionally not
implemented yet.
"""

from __future__ import annotations

import typer

from agentplatform import __version__
from agentplatform.spec import SpecError, load_spec

app = typer.Typer(
    name="agentctl",
    help="Declarative AI-agent platform CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Declarative AI-agent platform CLI."""


@app.command()
def version() -> None:
    """Print the installed agentplatform version."""
    typer.echo(__version__)


@app.command()
def validate(path: str) -> None:
    """Validate an agent YAML configuration without executing it."""
    try:
        loaded = load_spec(path)
    except SpecError as exc:
        typer.echo("✗ Configuration validation failed", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("✓ YAML loaded")
    typer.echo("✓ JSON schema valid")
    typer.echo("✓ Semantic validation passed")
    typer.echo(f"✓ Configuration is valid: {loaded.source_path}")


if __name__ == "__main__":  # pragma: no cover
    app()
