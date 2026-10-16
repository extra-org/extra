"""Atomic batch intake, callback deduplication, and durable receipt recovery."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel, select

from agent_manager.domain.channels.models import ChannelAddress, InboundMessage
from agent_manager.domain.channels.webhooks import DeliveryEvent, WebhookBatch, WebhookIssue
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelDeliveryEventRow,
    ChannelInboxRow,
    ChannelWebhookReceiptRow,
)
from agent_manager.infrastructure.persistence.channels.webhooks import SqlWebhookRepository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
MESSAGE = InboundMessage(
    ChannelAddress("connection", "system", "sender"), "message", "hello", NOW, NOW
)
BATCH = WebhookBatch(
    (MESSAGE,),
    (DeliveryEvent("outbound", "recipient", "delivered", NOW),),
    (WebhookIssue("entry.1", "invalid_entry"),),
)


@pytest.fixture
async def storage(tmp_path: Path) -> AsyncIterator[tuple[SqlWebhookRepository, AsyncEngine]]:
    database = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'receipts.db'}")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    try:
        yield SqlWebhookRepository(session_factory(database)), database
    finally:
        await database.dispose()


async def rows(database, model):
    async with session_factory(database)() as session:
        return list((await session.exec(select(model))).all())


async def accept(repository, *, digest="a" * 64, batch=BATCH, connection="connection"):
    return await repository.accept(connection, "system", digest, NOW, batch)


async def test_concurrent_callbacks_share_one_receipt_and_one_set_of_events(storage):
    repository, database = storage
    receipts = await asyncio.gather(*(accept(repository) for _ in range(8)))
    assert len({receipt.receipt_id for receipt in receipts}) == 1
    assert all(receipt == receipts[0] for receipt in receipts)
    for model in (
        ChannelWebhookReceiptRow,
        ChannelInboxRow,
        ChannelBindingRow,
        ChannelDeliveryEventRow,
    ):
        assert len(await rows(database, model)) == 1


async def test_differently_serialized_callbacks_deduplicate_normalized_messages_and_statuses(
    storage,
):
    repository, database = storage
    first = await accept(repository)
    second = await accept(repository, digest="b" * 64)
    assert first.receipt_id != second.receipt_id
    assert first.event_ids == second.event_ids
    assert first.delivery_event_ids == second.delivery_event_ids
    assert len(await rows(database, ChannelInboxRow)) == 1
    assert len(await rows(database, ChannelDeliveryEventRow)) == 1


async def test_delivery_write_failure_rolls_back_the_entire_callback(storage):
    repository, database = storage

    def fail_delivery(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO channel_delivery_events"):
            raise OSError("injected delivery storage failure")

    event.listen(database.sync_engine, "after_cursor_execute", fail_delivery)
    try:
        with pytest.raises(OSError):
            await accept(repository)
    finally:
        event.remove(database.sync_engine, "after_cursor_execute", fail_delivery)
    for model in (
        ChannelWebhookReceiptRow,
        ChannelInboxRow,
        ChannelBindingRow,
        ChannelDeliveryEventRow,
    ):
        assert await rows(database, model) == []
    committed = await accept(repository)
    assert len(committed.event_ids) == len(committed.delivery_event_ids) == 1


async def test_receipt_and_delivery_identity_survive_reopening(storage):
    repository, database = storage
    original = await accept(repository)
    url = database.url.render_as_string(hide_password=False)
    await database.dispose()
    reopened = create_db_engine(url)
    try:
        recovered_repository = SqlWebhookRepository(session_factory(reopened))
        assert await recovered_repository.get_receipt(original.receipt_id) == original
        assert await accept(recovered_repository) == original
        assert len(await rows(reopened, ChannelDeliveryEventRow)) == 1
    finally:
        await reopened.dispose()


async def test_separate_connections_never_share_callback_or_message_identity(storage):
    repository, database = storage
    first = await accept(repository)
    other_message = replace(MESSAGE, address=replace(MESSAGE.address, connection_id="other"))
    other = await accept(
        repository, connection="other", batch=replace(BATCH, messages=(other_message,))
    )
    assert first.receipt_id != other.receipt_id
    assert first.event_ids != other.event_ids
    assert first.delivery_event_ids != other.delivery_event_ids
    assert len(await rows(database, ChannelInboxRow)) == 2


async def test_conflicting_item_keeps_original_and_commits_valid_siblings(storage):
    repository, database = storage
    original = await accept(repository)
    conflict = replace(MESSAGE, address=replace(MESSAGE.address, sender_id="attacker"))
    another = replace(MESSAGE, provider_message_id="two")
    receipt = await accept(repository, digest="b" * 64, batch=WebhookBatch((conflict, another)))
    assert receipt.event_ids != original.event_ids
    assert receipt.issues[0].reason == "conflicting_message"
    assert [row.sender_id for row in await rows(database, ChannelBindingRow)] == ["sender"]
    assert len(await rows(database, ChannelInboxRow)) == 2


async def test_mismatched_batch_scope_is_rejected_without_writes(storage):
    repository, database = storage
    with pytest.raises(ValueError):
        await accept(repository, connection="other")
    assert await rows(database, ChannelWebhookReceiptRow) == []
    assert (
        await SqlChannelRepository(session_factory(database)).get_binding(MESSAGE.address) is None
    )
