from __future__ import annotations

import pytest

from agent_manager.config import Settings, normalize_database_url


def test_default_database_is_persistent_sqlite_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_DB_BACKEND", raising=False)
    monkeypatch.delenv("AGENT_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings()

    assert settings.agent_db_backend == "sqlite"
    assert settings.agent_db_url is None
    assert settings.effective_database_url == "sqlite+aiosqlite:///chat.db"
    assert settings.context_max_tokens is None


def test_sqlite_url_normalizes_to_async_driver() -> None:
    assert normalize_database_url("sqlite:///chat.db", "sqlite") == "sqlite+aiosqlite:///chat.db"


def test_postgres_url_normalizes_to_async_driver() -> None:
    assert (
        normalize_database_url("postgresql://u:p@localhost/db", "postgres")
        == "postgresql+asyncpg://u:p@localhost/db"
    )
