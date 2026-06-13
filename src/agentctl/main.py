from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.generate.generator import Generator
from agent_engine.parsers.yaml.parser import YAMLParser


@click.group()
def cli() -> None:
    """Declarative AI-agent platform CLI."""


@cli.command()
@click.option("--config", required=True, help="Path to agents.yml")
def validate(config: str) -> None:
    """Validate an agent YAML configuration without executing it."""
    try:
        spec = YAMLParser().parse(config)
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    base_dir = Path(config).resolve().parent
    errors = SystemSpecValidator().validate(spec, base_dir)
    if errors:
        for e in errors:
            click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    click.echo("✓ YAML valid")
    click.echo("✓ Engine validation passed")
    click.echo(f"✓ {config}")


@cli.command()
@click.option("--config", required=True, help="Path to agents.yml")
def generate(config: str) -> None:
    """Generate plugin stubs for tools and resolvers declared in the YAML."""
    try:
        spec = YAMLParser().parse(config)
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    base_dir = Path(config).resolve().parent
    result = Generator().generate(spec, base_dir)

    for name in result.created:
        click.echo(f"  create  {name}")
    for name in result.skipped:
        click.echo(f"  skip    {name}")

    if result.created:
        click.echo(f"\n✓ Created {len(result.created)} stub(s). Fill in the method bodies.")
    else:
        click.echo("✓ Nothing to generate — all stubs already exist.")


@cli.command()
@click.option("--config", required=True, help="Path to agents.yml")
@click.option("--message", required=True, help="User message")
@click.option("--env", default=None, help="Path to .env file")
@click.option("--stream", is_flag=True, help="Stream the answer")
def run(config: str, message: str, env: str | None, stream: bool) -> None:
    """Run a message through the agent system defined in the YAML."""
    asyncio.run(_run_async(config, message, env, stream))


async def _run_async(config: str, message: str, env: str | None, stream: bool) -> None:
    env_path = Path(env) if env else Path(config).resolve().parent / ".env"
    load_dotenv(env_path, override=True)

    try:
        spec = YAMLParser().parse(config)
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    base_dir = Path(config).resolve().parent
    errors = SystemSpecValidator().validate(spec, base_dir)
    if errors:
        for e in errors:
            click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    click.echo(f"  system : {spec.meta.name}", err=True)
    click.echo(f"  message: {message}", err=True)
    click.echo("", err=True)

    try:
        async with LangGraphEngine(base_dir) as engine:
            await engine.build(spec)
            if stream:
                async for event in await engine.stream(message):
                    if event.type == "route" and event.route:
                        click.echo(f"  route  : {' → '.join(event.route)}", err=True)
                    elif event.type == "answer_delta" and event.content:
                        sys.stdout.write(event.content)
                        sys.stdout.flush()
                sys.stdout.write("\n")
            else:
                result = await engine.run(message)
                click.echo(f"  route  : {' → '.join(result.visited)}", err=True)
                click.echo("")
                click.echo(result.answer)
    except Exception as exc:
        click.echo(f"✗ Runtime error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
