"""Durable channel work is isolated, ordered, and claimed once."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.composition import application_repositories
from agent_manager.config import Settings
from agent_manager.domain.channels.models import (
    ChannelAddress,
    ChannelClaimConflict,
    ChannelMessageConflict,
    ChannelOwnerConflict,
    InboundMessage,
    InboxState,
)
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory

NOW = datetime(2026, 10, 15, 10, tzinfo=UTC)
ADDRESS = ChannelAddress("number-1", "knowledge", "opaque-sender")


@pytest.fixture
async def storage(tmp_path: Path) -> AsyncIterator[tuple[SqlChannelRepository, AsyncEngine]]:
    database = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'work.db'}")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    try:
        yield SqlChannelRepository(session_factory(database)), database
    finally:
        await database.dispose()


def incoming(provider_id: str, *, address: ChannelAddress = ADDRESS) -> InboundMessage:
    return InboundMessage(address, provider_id, f"message:{provider_id}", NOW, NOW)


async def test_composed_storage_persists_and_recovers_an_inbound_message(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'channels.db'}"
    settings = Settings.from_values(database_url=url)
    database = create_db_engine(url)
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    await database.dispose()

    async with application_repositories(settings) as repositories:
        channels = repositories.channels
        assert channels is not None, "SQL composition must provide durable channel storage"

        original = await channels.ingest(
            InboundMessage(
                ChannelAddress("number-1", "knowledge", "opaque-sender"),
                "provider-event-1",
                "שלום",
                NOW,
                NOW,
            )
        )

    async with application_repositories(settings) as repositories:
        assert repositories.channels is not None
        recovered = await repositories.channels.get_inbound(original.event_id)
        assert recovered is not None
        assert recovered.message.content == "שלום"
        assert recovered.message.address.sender_id == "opaque-sender"


async def test_process_memory_has_no_durable_channel_backend() -> None:
    async with application_repositories(
        Settings.from_values(database_url="sqlite+aiosqlite:///:memory:")
    ) as repositories:
        assert repositories.channels is None


async def test_duplicate_intake_keeps_one_event_and_original_receive_order(storage) -> None:
    repository, _ = storage
    first = await repository.ingest(incoming("event-1"))
    duplicate = await repository.ingest(
        replace(incoming("event-1"), received_at=NOW.replace(hour=11))
    )
    second = await repository.ingest(incoming("event-2"))

    assert duplicate == first
    assert first.sequence < second.sequence
    claimed = await repository.claim_next("number-1", "knowledge")
    assert claimed is not None and claimed.event_id == first.event_id
    assert await repository.claim_next("number-1", "knowledge") is None


@pytest.mark.parametrize("changed", ["sender", "system", "content", "kind", "sent_at"])
async def test_provider_receipt_cannot_be_retargeted(storage, changed: str) -> None:
    repository, _ = storage
    original = incoming("one")
    stored = await repository.ingest(original)
    candidate = {
        "sender": replace(original, address=replace(ADDRESS, sender_id="other")),
        "system": replace(original, address=replace(ADDRESS, system_id="other")),
        "content": replace(original, content="changed"),
        "kind": replace(original, kind="reset"),
        "sent_at": replace(original, sent_at=NOW.replace(hour=9)),
    }[changed]
    with pytest.raises(ChannelMessageConflict):
        await repository.ingest(candidate)
    assert await repository.get_inbound(stored.event_id) == stored
    if changed in {"sender", "system"}:
        assert await repository.get_binding(candidate.address) is None


async def test_concurrent_intake_and_claim_have_one_owner(storage) -> None:
    repository, _ = storage
    items = await asyncio.gather(*[repository.ingest(incoming("same")) for _ in range(8)])
    assert len({item.event_id for item in items}) == 1
    claims = await asyncio.gather(
        *[repository.claim_next("number-1", "knowledge") for _ in range(8)]
    )
    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    assert winners[0].state is InboxState.PREPARING
    assert winners[0].claim_token


async def test_active_sender_does_not_block_another_sender_or_connection(storage) -> None:
    repository, _ = storage
    one = await repository.ingest(incoming("same"))
    await repository.ingest(incoming("later"))
    other_address = replace(ADDRESS, sender_id="other")
    other = await repository.ingest(incoming("different", address=other_address))
    other_connection = replace(ADDRESS, connection_id="number-2")
    separate = await repository.ingest(incoming("same", address=other_connection))

    first = await repository.claim_next("number-1", "knowledge")
    second = await repository.claim_next("number-1", "knowledge")
    third = await repository.claim_next("number-2", "knowledge")
    assert first is not None and first.event_id == one.event_id
    assert second is not None and second.event_id == other.event_id
    assert third is not None and third.event_id == separate.event_id


async def test_stale_claim_cannot_bind_finish_or_prepare(storage) -> None:
    repository, _ = storage
    item = await repository.ingest(incoming("one"))
    claimed = await repository.claim_next("number-1", "knowledge")
    assert claimed is not None
    with pytest.raises(ChannelClaimConflict):
        await repository.bind_conversation(item.event_id, "wrong", "alice", "org", "session")
    with pytest.raises(ChannelClaimConflict):
        await repository.record_prepared(item.event_id, "wrong", "session", "run", "message")
    with pytest.raises(ChannelClaimConflict):
        await repository.finish(item.event_id, "wrong", InboxState.DENIED)
    assert await repository.claim_execution(item.event_id, "wrong") is False
    assert (await repository.get_inbound(item.event_id)).state is InboxState.PREPARING


async def prepare_claim(repository: SqlChannelRepository, provider_id: str = "one"):
    await repository.ingest(incoming(provider_id))
    item = await repository.claim_next("number-1", "knowledge")
    assert item is not None and item.claim_token is not None
    await repository.bind_conversation(item.event_id, item.claim_token, "alice", "org", "session")
    prepared = await repository.record_prepared(
        item.event_id, item.claim_token, "session", "run", "message"
    )
    return prepared


async def test_preparation_and_execution_are_distinct_one_way_claims(storage) -> None:
    repository, _ = storage
    prepared = await prepare_claim(repository)
    token = prepared.claim_token
    assert token is not None
    replay = await repository.record_prepared(prepared.event_id, token, "session", "run", "message")
    assert replay == prepared
    with pytest.raises(ChannelClaimConflict):
        await repository.record_prepared(prepared.event_id, token, "session", "another", "message")
    results = await asyncio.gather(
        *[repository.claim_execution(prepared.event_id, token) for _ in range(6)]
    )
    assert results.count(True) == 1
    assert (await repository.get_inbound(prepared.event_id)).state is InboxState.RUNNING


async def test_ambiguous_work_blocks_later_turns_without_expiring(storage) -> None:
    repository, _ = storage
    prepared = await prepare_claim(repository)
    assert prepared.claim_token is not None
    await repository.ingest(incoming("later"))
    await repository.finish(prepared.event_id, prepared.claim_token, InboxState.NEEDS_REVIEW)
    assert await repository.claim_next("number-1", "knowledge") is None
    assert not await repository.claim_execution(prepared.event_id, prepared.claim_token)
    with pytest.raises(ChannelClaimConflict):
        await repository.finish(prepared.event_id, prepared.claim_token, InboxState.DONE)


@pytest.mark.parametrize("changed", ["user", "organization", "conversation"])
async def test_existing_binding_cannot_change_owner_or_conversation(storage, changed: str) -> None:
    repository, _ = storage
    prepared = await prepare_claim(repository)
    assert prepared.claim_token is not None
    user, organization, conversation = {
        "user": ("bob", "org", "session"),
        "organization": ("alice", "another", "session"),
        "conversation": ("alice", "org", "different"),
    }[changed]
    with pytest.raises(ChannelOwnerConflict):
        await repository.bind_conversation(
            prepared.event_id, prepared.claim_token, user, organization, conversation
        )
    binding = await repository.get_binding(ADDRESS)
    assert binding is not None
    assert (binding.user_id, binding.organization_id, binding.conversation_id) == (
        "alice",
        "org",
        "session",
    )


async def test_reset_is_ordered_and_advances_generation_once(storage) -> None:
    repository, _ = storage
    prepared = await prepare_claim(repository)
    assert prepared.claim_token is not None
    await repository.ingest(replace(incoming("reset"), kind="reset", content="/new"))
    await repository.ingest(incoming("after-reset"))
    assert await repository.claim_next("number-1", "knowledge") is None
    await repository.finish(prepared.event_id, prepared.claim_token, InboxState.FAILED)
    reset = await repository.claim_next("number-1", "knowledge")
    assert reset is not None and reset.claim_token is not None

    assert await repository.reset_conversation(reset.event_id, reset.claim_token) == 1
    assert await repository.reset_conversation(reset.event_id, reset.claim_token) == 1
    binding = await repository.get_binding(ADDRESS)
    assert binding is not None and binding.generation == 1
    assert binding.conversation_id is None and binding.user_id == "alice"
    following = await repository.claim_next("number-1", "knowledge")
    assert following is not None and following.message.provider_message_id == "after-reset"
    assert (await repository.get_inbound(prepared.event_id)).conversation_id == "session"


async def test_reply_staging_is_ordered_immutable_and_releases_next_turn(storage) -> None:
    repository, _ = storage
    prepared = await prepare_claim(repository)
    token = prepared.claim_token
    assert token is not None
    assert await repository.claim_execution(prepared.event_id, token)
    await repository.ingest(incoming("later"))
    replies = await repository.stage_reply(prepared.event_id, token, "assistant-1", ("שלום", "👋"))
    replay = await repository.stage_reply(prepared.event_id, token, "assistant-1", ("שלום", "👋"))
    assert replay == replies
    assert [(part.part_index, part.text) for part in replies] == [(0, "שלום"), (1, "👋")]
    assert len({part.reply_id for part in replies}) == 2
    with pytest.raises(ChannelMessageConflict):
        await repository.stage_reply(prepared.event_id, token, "assistant-2", ("different",))
    with pytest.raises(ChannelMessageConflict):
        await repository.stage_reply(prepared.event_id, token, "assistant-1", ("changed",))
    assert await repository.list_replies(prepared.event_id) == replies
    following = await repository.claim_next("number-1", "knowledge")
    assert following is not None and following.message.provider_message_id == "later"


async def test_outbox_failure_rolls_back_parts_and_keeps_sender_claim(storage) -> None:
    repository, database = storage
    prepared = await prepare_claim(repository)
    assert prepared.claim_token is not None
    assert await repository.claim_execution(prepared.event_id, prepared.claim_token)

    def fail_after_outbox_write(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO channel_outbox"):
            raise OSError("outbox unavailable")

    event.listen(database.sync_engine, "after_cursor_execute", fail_after_outbox_write)
    try:
        with pytest.raises(OSError, match="outbox unavailable"):
            await repository.stage_reply(
                prepared.event_id, prepared.claim_token, "answer", ("a", "b")
            )
    finally:
        event.remove(database.sync_engine, "after_cursor_execute", fail_after_outbox_write)
    assert await repository.list_replies(prepared.event_id) == ()
    assert (await repository.get_inbound(prepared.event_id)).state is InboxState.RUNNING
    binding = await repository.get_binding(ADDRESS)
    assert binding is not None and binding.active_event_id == prepared.event_id


async def test_reopening_storage_keeps_claims_and_reply_keys(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    database = create_db_engine(url)
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    repository = SqlChannelRepository(session_factory(database))
    prepared = await prepare_claim(repository)
    assert prepared.claim_token is not None
    assert await repository.claim_execution(prepared.event_id, prepared.claim_token)
    replies = await repository.stage_reply(
        prepared.event_id, prepared.claim_token, "answer", ("reply",)
    )
    await database.dispose()
    reopened = create_db_engine(url)
    try:
        repository = SqlChannelRepository(session_factory(reopened))
        assert await repository.list_replies(prepared.event_id) == replies
        assert not await repository.claim_execution(prepared.event_id, prepared.claim_token)
    finally:
        await reopened.dispose()


async def test_concurrent_preparation_bookkeeping_reuses_one_receipt(storage) -> None:
    repository, _ = storage
    await repository.ingest(incoming("one"))
    item = await repository.claim_next("number-1", "knowledge")
    assert item is not None and item.claim_token is not None
    await repository.bind_conversation(item.event_id, item.claim_token, "alice", "org", "session")
    results = await asyncio.gather(
        *[
            repository.record_prepared(item.event_id, item.claim_token, "session", "run", "message")
            for _ in range(8)
        ],
        return_exceptions=True,
    )
    assert all(not isinstance(result, Exception) for result in results), results
    assert all(result == results[0] for result in results)


async def test_concurrent_resets_advance_only_once(storage) -> None:
    repository, _ = storage
    await repository.ingest(replace(incoming("reset"), kind="reset", content="/new"))
    item = await repository.claim_next("number-1", "knowledge")
    assert item is not None and item.claim_token is not None
    generations = await asyncio.gather(
        *[repository.reset_conversation(item.event_id, item.claim_token) for _ in range(8)]
    )
    assert generations == [1] * 8
    assert (await repository.get_binding(ADDRESS)).generation == 1


async def test_concurrent_reply_staging_cannot_duplicate_parts(storage) -> None:
    repository, _ = storage
    item = await prepare_claim(repository)
    assert item.claim_token is not None
    assert await repository.claim_execution(item.event_id, item.claim_token)
    replies = await asyncio.gather(
        *[
            repository.stage_reply(item.event_id, item.claim_token, "answer", ("a", "b"))
            for _ in range(8)
        ]
    )
    assert all(result == replies[0] for result in replies)
    assert len(await repository.list_replies(item.event_id)) == 2


async def test_reply_cannot_be_staged_before_execution_claim(storage) -> None:
    repository, _ = storage
    item = await prepare_claim(repository)
    assert item.claim_token is not None
    with pytest.raises(ChannelClaimConflict):
        await repository.stage_reply(item.event_id, item.claim_token, "answer", ("a",))
    assert await repository.list_replies(item.event_id) == ()
    assert (await repository.get_inbound(item.event_id)).state is InboxState.PREPARED


async def test_inbox_failure_rolls_back_the_receipt_and_binding(storage) -> None:
    from sqlmodel import select

    from agent_manager.infrastructure.persistence.channels.tables import ChannelInboxRow

    repository, database = storage

    def fail_after_binding(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO channel_bindings"):
            raise OSError("binding unavailable")

    event.listen(database.sync_engine, "after_cursor_execute", fail_after_binding)
    try:
        with pytest.raises(OSError, match="binding unavailable"):
            await repository.ingest(incoming("one"))
    finally:
        event.remove(database.sync_engine, "after_cursor_execute", fail_after_binding)
    assert await repository.get_binding(ADDRESS) is None
    async with session_factory(database)() as session:
        assert list((await session.exec(select(ChannelInboxRow))).all()) == []
    retried = await repository.ingest(incoming("one"))
    assert retried.state is InboxState.QUEUED


@pytest.mark.parametrize(
    "change",
    [
        "empty_id",
        "long_id",
        "nul_id",
        "large_text",
        "empty_text",
        "nul_text",
        "naive_time",
        "unknown_kind",
    ],
)
def test_invalid_normalized_input_is_rejected_before_storage(change: str) -> None:
    with pytest.raises(ValueError):
        if change == "empty_id":
            replace(ADDRESS, sender_id=" ")
        elif change == "long_id":
            replace(ADDRESS, sender_id="x" * 513)
        elif change == "nul_id":
            replace(ADDRESS, sender_id="private\x00sender")
        elif change == "large_text":
            replace(incoming("one"), content="ש" * 32769)
        elif change == "empty_text":
            replace(incoming("one"), content=" ")
        elif change == "nul_text":
            replace(incoming("one"), content="private\x00text")
        elif change == "naive_time":
            replace(incoming("one"), received_at=NOW.replace(tzinfo=None))
        else:
            replace(incoming("one"), kind="unknown")  # type: ignore[arg-type]  # Unvalidated input.


async def test_unpersistable_reply_keeps_claim_and_writes_no_outbox_parts(storage) -> None:
    repository, _ = storage
    item = await prepare_claim(repository)
    assert await repository.claim_execution(item.event_id, item.claim_token)
    with pytest.raises(ValueError):
        await repository.stage_reply(
            item.event_id, item.claim_token, "answer", ("valid", "private\x00answer")
        )
    assert await repository.list_replies(item.event_id) == ()
    assert (await repository.get_inbound(item.event_id)).state is InboxState.RUNNING
    assert (await repository.get_binding(ADDRESS)).active_event_id == item.event_id


async def test_a_delayed_duplicate_reset_cannot_clear_a_newer_conversation(
    storage, monkeypatch
) -> None:
    repository, _ = storage
    previous = await prepare_claim(repository)
    assert previous.claim_token is not None
    await repository.finish(previous.event_id, previous.claim_token, InboxState.FAILED)
    await repository.ingest(replace(incoming("reset"), kind="reset", content="/new"))
    reset = await repository.claim_next("number-1", "knowledge")
    assert reset is not None and reset.claim_token is not None
    ready = asyncio.Event()
    release = asyncio.Event()
    slow: asyncio.Task[int] | None = None
    original_exec = AsyncSession.exec

    async def pause_before_reset_write(self, statement, *args, **kwargs):
        if asyncio.current_task() is slow and str(statement).startswith("UPDATE channel_inbox"):
            ready.set()
            await release.wait()
        return await original_exec(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "exec", pause_before_reset_write)
    slow = asyncio.create_task(repository.reset_conversation(reset.event_id, reset.claim_token))
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        assert await repository.reset_conversation(reset.event_id, reset.claim_token) == 1
        await repository.ingest(incoming("after-reset"))
        newer = await repository.claim_next("number-1", "knowledge")
        assert newer is not None and newer.claim_token is not None
        await repository.bind_conversation(
            newer.event_id, newer.claim_token, "alice", "org", "new-session"
        )
        release.set()
        assert await asyncio.wait_for(slow, timeout=2) == 1
    finally:
        release.set()
        if not slow.done():
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
    binding = await repository.get_binding(ADDRESS)
    assert binding is not None
    assert binding.conversation_id == "new-session"
    assert binding.active_event_id == newer.event_id
