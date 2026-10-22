"""Upgrade an existing staged reply and preserve it through downgrade."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from agent_manager.config import Settings
from agent_manager.infrastructure.persistence.channels.delivery import SqlDeliveryRepository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository


def test_delivery_migration_preserves_old_outbox_and_backfills_verified_window(
    tmp_path, monkeypatch
):
    path = tmp_path / "delivery.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setattr(
        "agent_manager.config.Settings", lambda: Settings.from_values(database_url=url)
    )
    migrations = (
        Path(__file__).parents[2] / "src/agent_manager/infrastructure/persistence/migrations"
    )
    config = Config(str(migrations / "alembic.ini"))
    config.set_main_option("script_location", str(migrations))
    command.upgrade(config, "0009")
    now = datetime(2026, 10, 16, tzinfo=UTC)

    async def session():
        database = create_db_engine(url)
        try:
            await SqlRepository(session_factory(database)).create_session(
                "conversation", user_id="owner"
            )
        finally:
            await database.dispose()

    asyncio.run(session())
    sync = create_engine(f"sqlite:///{path}")
    try:
        with sync.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO channel_bindings (connection_id, system_id, sender_id, "
                    "generation, user_id, organization_id, conversation_id) "
                    "VALUES ('connection','system','sender',0,'owner','org','conversation')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO channel_inbox (event_id, connection_id, system_id, sender_id, "
                    "provider_message_id, kind, content, received_at, sent_at, state, "
                    "conversation_id) VALUES ('event','connection','system','sender',"
                    "'inbound','text','question',:now,:now,'answered','conversation')"
                ),
                {"now": now.isoformat()},
            )
            connection.execute(
                text(
                    "INSERT INTO channel_outbox (reply_id, event_id, response_id, part_index, "
                    "text, created_at) VALUES ('reply','event','response',0,'saved answer',:now)"
                ),
                {"now": now.isoformat()},
            )
        command.upgrade(config, "head")

        async def recover():
            database = create_db_engine(url)
            try:
                delivery = SqlDeliveryRepository(session_factory(database))
                assert (await delivery.sources("connection", "system"))[0].texts == (
                    "saved answer",
                )
                await delivery.materialize("event", ("saved answer",))
                part = await delivery.claim_next("connection", "system", now)
                assert part is not None
                assert part.user_id == "owner" and await delivery.guard(part, now) is None
            finally:
                await database.dispose()

        asyncio.run(recover())
        command.downgrade(config, "0009")
        with sync.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT text FROM channel_outbox").scalar_one()
                == "saved answer"
            )
    finally:
        sync.dispose()
