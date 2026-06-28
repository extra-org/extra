from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import click

from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.generate.generator import Generator
from agent_engine.parsers.yaml.parser import YAMLParser
from agentctl.session import SpecError, load_and_validate, load_env


@click.group()
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Logging verbosity. Defaults to the LOG_LEVEL env var, else INFO.",
)
@click.pass_context
def cli(ctx: click.Context, log_level: str | None) -> None:
    """Declarative AI-agent platform CLI."""
    from agent_engine.logging_config import configure_logging

    configure_logging(log_level)


@cli.command()
@click.argument("config_path", type=click.Path())
def validate(config_path: str) -> None:
    """Validate an agent YAML spec offline (no LLM, no MCP network, no tools)."""
    from agentctl.diagnostics import format_validation_report, validate_spec

    result = validate_spec(config_path)
    click.echo(format_validation_report(result), err=not result.ok)
    if not result.ok:
        sys.exit(1)


@cli.command()
@click.argument("config_path", type=click.Path())
def inspect(config_path: str) -> None:
    """Print an offline summary of a spec (agents, MCPs, hooks, plugins, tags)."""
    from agentctl.diagnostics import inspect_spec

    try:
        click.echo(inspect_spec(config_path))
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)


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
@click.option(
    "--session-id",
    default=None,
    help="Stable conversation session id. Generated and printed if omitted.",
)
@click.option("--user-id", default=None, help="Optional user id for persisted local runs.")
def run(
    config: str,
    message: str,
    env: str | None,
    stream: bool,
    session_id: str | None,
    user_id: str | None,
) -> None:
    """Run a message through the agent system defined in the YAML."""
    load_env(config, env)
    from agent_manager.infrastructure.persistence.database import upgrade_database

    upgrade_database()
    asyncio.run(_run_async(config, message, env, stream, session_id, user_id))


async def _run_async(
    config: str,
    message: str,
    env: str | None,
    stream: bool,
    session_id: str | None = None,
    user_id: str | None = None,
) -> None:
    load_env(config, env)

    try:
        spec, base_dir = load_and_validate(config)
    except SpecError as exc:
        for message_text in exc.messages:
            click.echo(f"✗ {message_text}", err=True)
        sys.exit(1)

    from agent_manager.application import ConversationService
    from agent_manager.config import Settings
    from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
    from agent_manager.infrastructure.persistence.sql_repository import SqlRepository

    effective_session_id = session_id or uuid4().hex[:16]

    click.echo(f"  system : {spec.meta.name}", err=True)
    if session_id:
        click.echo(f"  session: {effective_session_id}", err=True)
    else:
        click.echo(
            f"  session: {effective_session_id} (generated; reuse with --session-id)",
            err=True,
        )
    click.echo(f"  message: {message}", err=True)
    click.echo("", err=True)

    settings = Settings()
    db_engine = create_db_engine(settings.effective_database_url)
    repository = SqlRepository(session_factory(db_engine))
    try:
        async with LangGraphEngine(base_dir) as engine:
            await engine.build(spec)
            service = ConversationService(
                engine,
                repository,
                window=settings.context_window,
                max_chars=settings.context_max_chars,
                snapshot_ttl_seconds=settings.snapshot_ttl_seconds,
                system_name=spec.meta.name,
                config_path=str(Path(config).resolve()),
            )
            await service.create(user_id=user_id, session_id=effective_session_id)
            if stream:
                async for event in service.stream(effective_session_id, message, user_id=user_id):
                    if event.type == "route" and event.route:
                        click.echo(f"  route  : {' → '.join(event.route)}", err=True)
                    elif event.type == "answer_delta" and event.content:
                        sys.stdout.write(event.content)
                        sys.stdout.flush()
                sys.stdout.write("\n")
            else:
                result = await service.send(effective_session_id, message, user_id=user_id)
                click.echo(f"  route  : {' → '.join(result.visited)}", err=True)
                click.echo("")
                click.echo(result.answer)
    except Exception as exc:
        click.echo(f"✗ Runtime error: {exc}", err=True)
        sys.exit(1)
    finally:
        await db_engine.dispose()


@cli.command()
@click.option("--config", required=True, help="Path to agents.yml")
@click.option("--host", default="0.0.0.0", show_default=True, help="Host to bind to")
@click.option("--port", default=8080, show_default=True, help="Port to listen on")
@click.option("--env", default=None, help="Path to .env file")
def serve(config: str, host: str, port: int, env: str | None) -> None:
    """Serve the agent system as an HTTP API."""
    import uvicorn

    from agent_engine.api.app import create_app

    load_env(config, env)

    app = create_app(config)
    uvicorn.run(app, host=host, port=port)


@cli.command()
@click.option("--config", default=None, help="Path to agents.yml (local engine mode)")
@click.option("--url", default=None, help="Base URL of a running `agentctl serve` (remote mode)")
@click.option("--env", default=None, help="Path to .env file (local mode only)")
@click.option("--stream", is_flag=True, help="Stream answers token by token")
@click.option(
    "--session-id",
    default=None,
    help="Tracing session id grouping this console (Langfuse session). Auto-generated if omitted.",
)
def chat(
    config: str | None, url: str | None, env: str | None, stream: bool, session_id: str | None
) -> None:
    """Interactive simulation console — keep the engine (or a server) running
    and ask questions in a loop.

    Pass exactly one of --config (build the engine locally) or --url (talk to a
    running `agentctl serve`). Type 'exit', 'quit', or 'q' (or Ctrl-C/Ctrl-D)
    to stop. Every question in the console shares one tracing session.
    """
    if config and url:
        raise click.UsageError("Pass exactly one of --config or --url, not both.")
    if not config and not url:
        raise click.UsageError("Pass one of --config (local engine) or --url (remote server).")

    from agentctl.chat import run_local_chat, run_remote_chat

    if config:
        asyncio.run(run_local_chat(config, env, stream, session_id=session_id))
    else:
        assert url is not None
        asyncio.run(run_remote_chat(url, stream, session_id=session_id))


if __name__ == "__main__":
    cli()
