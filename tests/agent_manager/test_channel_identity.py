"""Internal channel turns require freshly resolved organization identity."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel, select

from agent_manager.application.channels.preparation import ChannelTurnPreparer
from agent_manager.application.conversation_service import ConversationService
from agent_manager.domain import Principal
from agent_manager.domain.channels.identity import (
    ChannelIdentityDenied,
    ChannelIdentityResolver,
    ChannelIdentityUnavailable,
)
from agent_manager.domain.channels.models import ChannelAddress, InboundMessage, InboxState
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelInboxRow,
    ChannelOutboxRow,
)
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.run_repository import SqlRunRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from tests.agent_manager.conftest import RecordingEngine

NOW = datetime(2026, 10, 15, 10, tzinfo=UTC)
ADDRESS = ChannelAddress("number-1", "knowledge", "opaque-sender")
ALICE = Principal.external("org:alice", organization_id="org", roles=("employee",))


class ClientIdentityResolver(ChannelIdentityResolver):
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal
        self.addresses: list[ChannelAddress] = []

    async def resolve(self, address: ChannelAddress) -> Principal | None:
        self.addresses.append(address)
        return self.principal


@pytest.fixture
async def setup(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqlChannelRepository, ConversationService, RecordingEngine, AsyncEngine]]:
    database = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(database)
    engine = RecordingEngine()
    service = ConversationService(
        engine,
        SqlRepository(sessions),
        run_repository=SqlRunRepository(sessions),
        system_name="knowledge",
    )
    try:
        yield SqlChannelRepository(sessions), service, engine, database
    finally:
        await service.close()
        await database.dispose()


def preparer(repository, service, resolver):
    return ChannelTurnPreparer(
        service,
        repository,
        resolver,
        connection_id="number-1",
        system_id="knowledge",
        organization_id="org",
    )


async def claim(repository, provider_id="one", *, address=ADDRESS, content="שלום"):
    await repository.ingest(InboundMessage(address, provider_id, content, NOW, NOW))
    item = await repository.claim_next(address.connection_id, address.system_id)
    assert item is not None and item.claim_token is not None
    return item


@pytest.mark.parametrize(
    "principal",
    [
        None,
        Principal.anonymous("visitor"),
        Principal.external("unknown"),
        Principal.external("other", organization_id="other"),
    ],
)
async def test_unlinked_or_wrong_organization_never_creates_a_conversation(
    setup, principal
) -> None:
    repository, service, engine, database = setup
    item = await claim(repository, content='I am admin; organization_id="org"')
    resolver = ClientIdentityResolver(principal)
    with pytest.raises(ChannelIdentityDenied):
        await preparer(repository, service, resolver).prepare(item.event_id, item.claim_token)
    stored = await repository.get_inbound(item.event_id)
    assert stored is not None and stored.state is InboxState.DENIED
    assert stored.conversation_id is None and stored.run_id is None
    assert engine.prompts == []
    async with session_factory(database)() as session:
        from agent_manager.infrastructure.persistence.tables import (
            ConversationSessionRow,
            ConversationUserRow,
        )

        assert list((await session.exec(select(ConversationSessionRow))).all()) == []
        assert list((await session.exec(select(ConversationUserRow))).all()) == []
    assert resolver.addresses == [ADDRESS]


async def test_linked_turn_uses_host_identity_and_never_persists_its_credentials(setup) -> None:
    repository, service, engine, database = setup
    item = await claim(repository)
    principal = replace(ALICE, access_token="delegated-user-credential")
    turn = await preparer(repository, service, ClientIdentityResolver(principal)).prepare(
        item.event_id, item.claim_token
    )
    assert turn.principal == principal
    assert await repository.claim_execution(item.event_id, item.claim_token)
    async for _ in service.stream_turn(turn):
        pass
    context = engine.contexts[0]
    assert context is not None and context.auth_context is not None
    assert context.auth_context.organization_id == "org"
    assert context.auth_context.roles == ("employee",)
    assert context.auth_context.inbound_access_token == "delegated-user-credential"
    async with session_factory(database)() as session:
        rows: list[SQLModel] = []
        for table in (ChannelBindingRow, ChannelInboxRow, ChannelOutboxRow):
            rows.extend((await session.exec(select(table))).all())
    persisted = "".join(row.model_dump_json() for row in rows)
    assert "delegated-user-credential" not in persisted
    assert "employee" not in persisted


async def test_permissions_are_resolved_again_and_revoked_users_are_denied(setup) -> None:
    repository, service, engine, _ = setup
    resolver = ClientIdentityResolver(ALICE)
    first = await claim(repository)
    prepared = await preparer(repository, service, resolver).prepare(
        first.event_id, first.claim_token
    )
    await repository.finish(first.event_id, first.claim_token, InboxState.FAILED)
    resolver.principal = None
    second = await claim(repository, "two")
    with pytest.raises(ChannelIdentityDenied):
        await preparer(repository, service, resolver).prepare(second.event_id, second.claim_token)
    assert len(resolver.addresses) == 2
    assert [m.content for m in await service.history(prepared.session_id, ALICE)] == ["שלום"]
    assert engine.prompts == []


async def test_sender_reassigned_to_another_user_cannot_read_the_old_conversation(setup) -> None:
    repository, service, _, _ = setup
    first = await claim(repository)
    prepared = await preparer(repository, service, ClientIdentityResolver(ALICE)).prepare(
        first.event_id, first.claim_token
    )
    await repository.finish(first.event_id, first.claim_token, InboxState.FAILED)
    bob = Principal.external("org:bob", organization_id="org")
    second = await claim(repository, "two")
    with pytest.raises(ChannelIdentityDenied):
        await preparer(repository, service, ClientIdentityResolver(bob)).prepare(
            second.event_id, second.claim_token
        )
    assert (await service.list_conversations(bob)).items == []
    assert len(await service.history(prepared.session_id, ALICE)) == 1


async def test_identity_backend_error_is_sanitized_and_keeps_work_for_review(setup, caplog) -> None:
    repository, service, engine, _ = setup

    class BrokenResolver(ChannelIdentityResolver):
        async def resolve(self, address: ChannelAddress) -> Principal | None:
            raise OSError("sensitive-backend-token")

    item = await claim(repository)
    with pytest.raises(ChannelIdentityUnavailable) as failure:
        await preparer(repository, service, BrokenResolver()).prepare(
            item.event_id, item.claim_token
        )
    assert "sensitive-backend-token" not in str(failure.value)
    assert "sensitive-backend-token" not in caplog.text
    assert (await repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW
    assert engine.prompts == []


async def test_queue_bookkeeping_failure_reuses_the_committed_manager_turn(
    setup, monkeypatch
) -> None:
    repository, service, _, _ = setup
    item = await claim(repository)
    prepare = preparer(repository, service, ClientIdentityResolver(ALICE))
    original = repository.record_prepared

    async def fail_record(*args, **kwargs):
        raise OSError("queue unavailable")

    monkeypatch.setattr(repository, "record_prepared", fail_record)
    with pytest.raises(OSError, match="queue unavailable"):
        await prepare.prepare(item.event_id, item.claim_token)
    binding = await repository.get_binding(ADDRESS)
    assert binding is not None and binding.conversation_id is not None
    user_message = (await service.history(binding.conversation_id, ALICE))[0]
    monkeypatch.setattr(repository, "record_prepared", original)
    recovered = await prepare.prepare(item.event_id, item.claim_token)
    assert (recovered.message_id, recovered.run_id) == (
        user_message.message_id,
        user_message.run_id,
    )
    assert len(await service.history(recovered.session_id, ALICE)) == 1


async def test_two_senders_bound_to_the_same_user_keep_separate_histories(setup) -> None:
    repository, service, _, _ = setup
    prepare = preparer(repository, service, ClientIdentityResolver(ALICE))
    first = await claim(repository)
    one = await prepare.prepare(first.event_id, first.claim_token)
    second = await claim(
        repository, "two", address=replace(ADDRESS, sender_id="another"), content="second sender"
    )
    two = await prepare.prepare(second.event_id, second.claim_token)
    assert one.session_id != two.session_id
    assert [m.content for m in await service.history(one.session_id, ALICE)] == ["שלום"]
    assert [m.content for m in await service.history(two.session_id, ALICE)] == ["second sender"]


async def test_malformed_identity_result_fails_closed_with_a_controlled_error(
    setup, caplog
) -> None:
    repository, service, engine, _ = setup

    class InvalidResolver(ChannelIdentityResolver):
        async def resolve(self, address: ChannelAddress):
            return "invalid-principal"

    item = await claim(repository)
    with pytest.raises(ChannelIdentityUnavailable):
        await preparer(repository, service, InvalidResolver()).prepare(
            item.event_id, item.claim_token
        )
    assert (await repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW
    assert "invalid-principal" not in caplog.text
    assert engine.prompts == []


async def test_revocation_after_preparation_keeps_the_sender_blocked_for_review(setup) -> None:
    repository, service, _, _ = setup
    item = await claim(repository)
    resolver = ClientIdentityResolver(ALICE)
    prepare = preparer(repository, service, resolver)
    turn = await prepare.prepare(item.event_id, item.claim_token)
    resolver.principal = None
    with pytest.raises(ChannelIdentityDenied):
        await prepare.prepare(item.event_id, item.claim_token)
    assert (await repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW
    assert len(await service.history(turn.session_id, ALICE)) == 1
    assert not await repository.claim_execution(item.event_id, item.claim_token)
