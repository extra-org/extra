from __future__ import annotations

from agent_manager.config import normalize_database_url


def test_sqlite_url_normalizes_to_async_driver() -> None:
    assert normalize_database_url("sqlite:///chat.db", "sqlite") == "sqlite+aiosqlite:///chat.db"


def test_postgres_url_normalizes_to_async_driver() -> None:
    assert (
        normalize_database_url("postgresql://u:p@localhost/db", "postgres")
        == "postgresql+asyncpg://u:p@localhost/db"
    )
