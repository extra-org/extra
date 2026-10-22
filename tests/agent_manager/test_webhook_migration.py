"""Upgrade/downgrade preserves WA-02 input while adding durable callbacks."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from agent_manager.config import Settings
from agent_manager.domain.channels.models import ChannelAddress, InboundMessage, stable_id
from agent_manager.domain.channels.webhooks import DeliveryEvent, WebhookBatch
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.channels.webhooks import SqlWebhookRepository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory


def test_webhook_migration_preserves_existing_channel_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "webhook-migration.db"
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
    command.upgrade(config, "0008")

    async def intake(*, webhook=False, legacy=False):
        database = create_db_engine(url)
        try:
            sessions = session_factory(database)
            timestamp = datetime(2026, 10, 15, tzinfo=UTC)
            if legacy:
                # Freeze the historical fixture at 0008 rather than invoking
                # today's adapter before its required migrations exist.
                event_id = stable_id("inbox", "connection", "existing")
                async with database.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO channel_bindings "
                            "(connection_id, system_id, sender_id, generation) "
                            "VALUES ('connection','system','sender',0) ON CONFLICT DO NOTHING"
                        )
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO channel_inbox (event_id, connection_id, system_id, "
                            "sender_id, provider_message_id, kind, content, received_at, "
                            "sent_at, state) VALUES (:event,'connection','system',"
                            "'sender','existing','text','hello',"
                            ":now,:now,'queued') ON CONFLICT DO NOTHING"
                        ),
                        {"event": event_id, "now": timestamp.isoformat()},
                    )
                return event_id
            item = await SqlChannelRepository(sessions).ingest(
                InboundMessage(
                    ChannelAddress("connection", "system", "sender"),
                    "existing",
                    "hello",
                    timestamp,
                    timestamp,
                )
            )
            if webhook:
                result = await SqlWebhookRepository(sessions).accept(
                    "connection",
                    "system",
                    "a" * 64,
                    timestamp,
                    WebhookBatch(
                        delivery_events=(DeliveryEvent("outbound", "sender", "sent", timestamp),)
                    ),
                )
                assert len(result.delivery_event_ids) == 1
            return item.event_id
        finally:
            await database.dispose()

    original = asyncio.run(intake(legacy=True))
    command.upgrade(config, "head")
    assert asyncio.run(intake(webhook=True)) == original
    sync_database = create_engine(f"sqlite:///{path}")
    try:
        with sync_database.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM channel_webhook_receipts"
                ).scalar_one()
                == 1
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM channel_delivery_events"
                ).scalar_one()
                == 1
            )
    finally:
        sync_database.dispose()
    command.downgrade(config, "0008")
    assert asyncio.run(intake(legacy=True)) == original
    sync_database = create_engine(f"sqlite:///{path}")
    try:
        assert "channel_webhook_receipts" not in inspect(sync_database).get_table_names()
    finally:
        sync_database.dispose()
