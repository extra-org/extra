"""The channel schema is explicitly migrated and usable after reopening SQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine
from sqlmodel import SQLModel

from agent_manager.config import Settings
from agent_manager.domain.channels.models import ChannelAddress, InboundMessage
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.database import (
    create_db_engine,
    session_factory,
    upgrade_database,
)
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository


def test_upgrade_and_downgrade_preserve_existing_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "migration.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setattr(
        "agent_manager.config.Settings", lambda: Settings.from_values(database_url=url)
    )
    migrations = (
        Path(__file__).resolve().parents[2]
        / "src/agent_manager/infrastructure/persistence/migrations"
    )
    config = Config(str(migrations / "alembic.ini"))
    config.set_main_option("script_location", str(migrations))
    command.upgrade(config, "0007")

    async def existing_conversation(*, create: bool = False) -> bool:
        engine = create_db_engine(url)
        try:
            repository = SqlRepository(session_factory(engine))
            if create:
                await repository.create_session("existing")
            return await repository.conversation_exists("existing")
        finally:
            await engine.dispose()

    assert asyncio.run(existing_conversation(create=True))
    assert upgrade_database()

    async def use_channels() -> str:
        engine = create_db_engine(url)
        try:
            repository = SqlChannelRepository(session_factory(engine))
            timestamp = datetime(2026, 10, 15, tzinfo=UTC)
            item = await repository.ingest(
                InboundMessage(
                    ChannelAddress("connection", "system", "sender"),
                    "event",
                    "hello",
                    timestamp,
                    timestamp,
                )
            )
            claimed = await repository.claim_next("connection", "system")
            assert claimed is not None and claimed.claim_token is not None
            await repository.bind_conversation(
                item.event_id, claimed.claim_token, "user", "org", "existing"
            )
            await repository.record_prepared(
                item.event_id, claimed.claim_token, "existing", "run", "message"
            )
            assert await repository.claim_execution(item.event_id, claimed.claim_token)
            replies = await repository.stage_reply(
                item.event_id, claimed.claim_token, "answer", ("saved",)
            )
            return replies[0].reply_id
        finally:
            await engine.dispose()

    reply_id = asyncio.run(use_channels())
    database = create_engine(f"sqlite:///{path}")
    try:
        with database.connect() as connection:
            channel_schema = MetaData()
            for table in SQLModel.metadata.sorted_tables:
                if table.name.startswith("channel_"):
                    table.to_metadata(channel_schema)
            differences = compare_metadata(
                MigrationContext.configure(
                    connection,
                    opts={
                        "include_name": lambda name, kind, parents: (
                            kind != "table" or name.startswith("channel_")
                        )
                    },
                ),
                channel_schema,
            )
            assert differences == []
            assert (
                connection.exec_driver_sql("SELECT reply_id FROM channel_outbox").scalar_one()
                == reply_id
            )
    finally:
        database.dispose()
    command.downgrade(config, "0007")
    assert asyncio.run(existing_conversation())
    assert upgrade_database()
    assert asyncio.run(existing_conversation())
