"""Composition root + `agent-manager` console entrypoint.

Self-contained: does not import `agentctl`, so `agent_manager` depends only on
`agent_engine`.
"""

from __future__ import annotations

from pathlib import Path

import click
from dotenv import load_dotenv

_MIGRATIONS = Path(__file__).resolve().parent / "infrastructure" / "persistence" / "migrations"


def _upgrade_db() -> None:
    """Bring the database to the latest schema via Alembic (uses DATABASE_URL)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_MIGRATIONS / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    command.upgrade(cfg, "head")


@click.command()
@click.option("--config", required=True, help="Path to agents.yml")
@click.option("--host", default=None, help="Host to bind to (overrides settings)")
@click.option("--port", default=None, type=int, help="Port to listen on (overrides settings)")
@click.option("--env", default=None, help="Path to .env file (defaults to .env beside config)")
@click.option("--migrate/--no-migrate", default=True, help="Run DB migrations before serving")
def main(config: str, host: str | None, port: int | None, env: str | None, migrate: bool) -> None:
    """Serve the chat lifecycle API (sessions + history) over HTTP."""
    import uvicorn

    from agent_manager.api import create_app
    from agent_manager.config import Settings

    env_path = Path(env) if env else Path(config).resolve().parent / ".env"
    load_dotenv(env_path, override=True)

    settings = Settings()
    if migrate:
        _upgrade_db()

    app = create_app(config, settings)
    uvicorn.run(app, host=host or settings.host, port=port or settings.port)


if __name__ == "__main__":
    main()
