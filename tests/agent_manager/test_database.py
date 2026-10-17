"""Database setup runs migrations only against a database that outlives the process."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from agent_manager.config import Settings
from agent_manager.infrastructure.persistence.database import (
    create_db_engine,
    session_factory,
    upgrade_database,
)
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from agent_manager.infrastructure.persistence.tables import (
    ConversationMessageRow,
    ConversationRunTokenUsageRow,
    ConversationSessionRow,
    RunRecordRow,
)


def test_upgrade_is_a_noop_for_an_in_memory_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_manager.config.Settings",
        lambda: Settings.from_values(database_url="sqlite+aiosqlite:///:memory:"),
    )

    assert upgrade_database() is False


@pytest.mark.parametrize("has_run_table", [True, False], ids=["current", "legacy-no-runs"])
def test_usage_migration_backfills_pending_cancelled_and_completed_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_run_table: bool
) -> None:
    database_path = tmp_path / "usage.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setattr(
        "agent_manager.config.Settings", lambda: Settings.from_values(database_url=url)
    )
    migrations = (
        Path(__file__).resolve().parents[2]
        / "src/agent_manager/infrastructure/persistence/migrations"
    )
    config = Config(str(migrations / "alembic.ini"))
    config.set_main_option("script_location", str(migrations))
    command.upgrade(config, "0005")

    database = create_engine(f"sqlite:///{database_path}")
    now = datetime.now(UTC)
    with Session(database) as session:
        session.add(ConversationSessionRow(session_id="charged", created_at=now, updated_at=now))
        for run_id, status, tokens in (
            ("cancelled", "cancelled", 5),
            ("pending", "pending_approval", 3),
            ("completed", "completed", 7),
        ):
            session.add(
                RunRecordRow(
                    run_id=run_id,
                    thread_id=run_id,
                    system_name="legacy",
                    status=status,
                    input_tokens=tokens,
                    output_tokens=0,
                    created_at=0,
                    updated_at=0,
                )
            )
            session.add(
                ConversationMessageRow(
                    message_id=f"user-{run_id}",
                    session_id="charged",
                    run_id=run_id,
                    role="user",
                    content="request",
                    created_at=now,
                )
            )
        for message_run_id, tokens in (("completed", 7), ("legacy-without-run", 11), (None, 2)):
            session.add(
                ConversationMessageRow(
                    message_id=f"assistant-{message_run_id}",
                    session_id="charged",
                    run_id=message_run_id,
                    role="assistant",
                    content="answer",
                    input_tokens=tokens,
                    created_at=now,
                )
            )
        session.commit()

    if not has_run_table:
        SQLModel.metadata.tables["agent_runs"].drop(database)
    assert upgrade_database() is True

    with Session(database) as session:
        charges = session.exec(select(ConversationRunTokenUsageRow)).all()
        expected_charges = {"cancelled": 5, "pending": 3, "completed": 7} if has_run_table else {}
        assert {row.run_id: row.total_tokens for row in charges} == expected_charges

    async def usage_after_restart() -> int:
        engine = create_db_engine(url)
        try:
            return await SqlRepository(session_factory(engine)).get_token_usage("charged")
        finally:
            await engine.dispose()

    expected_usage = 28 if has_run_table else 20
    assert asyncio.run(usage_after_restart()) == expected_usage
    command.downgrade(config, "0005")
    assert upgrade_database() is True
    assert asyncio.run(usage_after_restart()) == expected_usage
    database.dispose()
