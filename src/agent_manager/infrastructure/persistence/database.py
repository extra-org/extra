"""Async database engine + session factory, built from a connection URL."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def create_db_engine(url: str) -> AsyncEngine:
    if ":memory:" in url:
        # One shared connection, or each session gets its own empty in-memory db.
        return create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    return create_async_engine(url)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def upgrade_database() -> None:
    """Bring the configured database to the latest schema via Alembic."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_MIGRATIONS / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    command.upgrade(cfg, "head")
