"""CLI entrypoint for the declarative AI-agent platform.

This module is intentionally thin: it owns only the Typer app wiring, argument
parsing, user-facing output, and exit codes.  All logic lives in dedicated
modules next to this file (``generate.py``, ``run.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from agentplatform import __version__
from agentplatform.runtime.engine import Engine
from agentplatform.spec import SpecError, load_spec
from agentplatform.spec.stubs import ResolverGenerateMode, generate_stubs

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
def generate(
    path: str = typer.Argument(..., help="Path to agents.yml"),
    mode: str = typer.Option(
        "all",
        "--mode",
        help="Resolver generation mode: 'all', 'children', or 'child'.",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Agent id to generate (required when --mode=child).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "--overwrite",
        help="Overwrite existing generated files instead of preserving them.",
    ),
) -> None:
    """Generate plugin stubs for tools and resolvers declared in the YAML.

    Creates ``plugins/tools/{id}.py`` plus the resolver class/config files next
    to the YAML file. Existing files are never overwritten by default — only
    missing stubs are added. Use --force to regenerate.

    Generation modes control which resolver files are affected:

    \b
      --mode all       Regenerate everything (base, all children, TOML).
      --mode children  Generate/update all child resolver classes only.
      --mode child     Generate/update one specific child (requires --agent).
    """
    try:
        resolver_mode = ResolverGenerateMode(mode)
    except ValueError as exc:
        typer.echo(f"✗ Invalid mode '{mode}'. Must be one of: all, children, child.", err=True)
        raise typer.Exit(code=1) from exc

    try:
        loaded = load_spec(path)
    except SpecError as exc:
        typer.echo("✗ Configuration validation failed", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        result = generate_stubs(
            loaded.source_path.parent,
            loaded.spec,
            resolver_mode=resolver_mode,
            resolver_agent_id=agent,
            overwrite=force,
        )
    except ValueError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for rel in result.created:
        typer.echo(f"  create  {rel}")
    for rel in result.updated:
        typer.echo(f"  update  {rel}")
    for rel in result.stale:
        typer.echo(f"  stale   {rel}")

    changed = len(result.created) + len(result.updated)
    if changed:
        typer.echo(f"\n✓ Generated {changed} stub update(s). Fill in the method bodies.")


@app.command()
def run(
    path: str = typer.Argument(..., help="Path to agents.yml"),
    message: str = typer.Argument(..., help="Message to send to the agent system"),
    env: str | None = typer.Option(None, "--env", help="Path to .env (default: next to YAML)"),
) -> None:
    """Run a message through the agent system defined in the YAML.

    Loads the YAML, compiles the graph in memory, loads plugins and prompts,
    then invokes the agent with the given message and prints the answer.

    API keys are read from the .env file next to the YAML (or --env path).
    """
    env_path = Path(env) if env else Path(path).resolve().parent / ".env"
    load_dotenv(env_path, override=True)
    typer.echo(f"  env    : {env_path}", file=sys.stderr)

    try:
        loaded = load_spec(path)
    except SpecError as exc:
        typer.echo("✗ Configuration invalid", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        result = Engine(loaded).run(message)
    except Exception as exc:
        typer.echo(f"✗ Runtime error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"  system : {result.system_name}", err=True)
    typer.echo(f"  message: {message}", err=True)
    typer.echo("", err=True)
    typer.echo(f"  route  : {' → '.join(result.visited)}", err=True)
    typer.echo("", err=True)
    typer.echo(result.answer)  # stdout — pipeable


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
