"""Retrying preparation recovers one immutable turn, never a second execution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from sqlmodel import SQLModel

from agent_engine.approvals.models import RunRecord, RunStatus
from agent_engine.engine.types import ChatMessage, ChatRole, RunResult
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runs.repository import RunRepository
from agent_manager.application.conversation_service import ConversationService
from agent_manager.application.errors import (
    ConversationAccessDenied,
    ConversationTokenBudgetExceeded,
)
from agent_manager.domain import ConversationMessage, Principal, Repository, Role
from agent_manager.domain.turn_preparation import (
    IdempotentTurnRepository,
    TurnPreparationConflict,
    preparation_key_digest,
)
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.run_repository import SqlRunRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from tests.agent_manager.conftest import RecordingEngine

ALICE = Principal.external("alice")
BOB = Principal.external("bob")


@pytest.fixture(params=["memory", "sql"])
async def storage(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[Repository, RunRepository]]:
    if request.param == "memory":
        yield MemoryRepository(), InMemoryRunRepository()
        return
    database = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'turns.db'}")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(database)
    try:
        yield SqlRepository(sessions), SqlRunRepository(sessions)
    finally:
        await database.dispose()


def service_for(
    storage: tuple[Repository, RunRepository], *, max_tokens: int | None = None
) -> ConversationService:
    return ConversationService(
        RecordingEngine(),
        storage[0],
        run_repository=storage[1],
        system_name="test",
        max_tokens=max_tokens,
    )


async def test_retry_recovers_original_ids_and_parent_history_after_head_moves(
    storage: tuple[Repository, RunRepository],
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    await service.send(conversation, "earlier", ALICE)
    first = await service.prepare_turn(conversation, "שלום 👋", ALICE, idempotency_key="event-1")
    await service.complete_turn(first, RunResult("test", [], "תשובה"))
    await service.send(conversation, "later", ALICE)

    recovered = await service.prepare_turn(
        conversation, "שלום 👋", ALICE, idempotency_key="event-1"
    )

    assert (recovered.message_id, recovered.run_id) == (first.message_id, first.run_id)
    assert recovered.history == (
        ChatMessage(ChatRole.USER, "earlier"),
        ChatMessage(ChatRole.ASSISTANT, "answer:earlier"),
    )
    assert [m.content for m in await service.history(conversation, ALICE)] == [
        "earlier",
        "answer:earlier",
        "שלום 👋",
        "תשובה",
        "later",
        "answer:later",
    ]


@pytest.mark.parametrize("change", ["content", "conversation", "owner"])
async def test_key_cannot_be_rebound_to_different_input(
    storage: tuple[Repository, RunRepository], change: str
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    await service.prepare_turn(conversation, "original", ALICE, idempotency_key="one-key")
    principal = BOB if change == "owner" else ALICE
    target = await service.create(principal) if change != "content" else conversation

    with pytest.raises(TurnPreparationConflict, match=r"[Ii]dempotency"):
        await service.prepare_turn(
            target,
            "changed" if change == "content" else "original",
            principal,
            idempotency_key="one-key",
        )

    assert [m.content for m in await service.history(conversation, ALICE)] == ["original"]
    if target != conversation:
        assert await service.history(target, principal) == []


async def test_receipt_does_not_bypass_conversation_ownership(
    storage: tuple[Repository, RunRepository],
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    await service.prepare_turn(conversation, "private", ALICE, idempotency_key="one-key")
    with pytest.raises(ConversationAccessDenied):
        await service.prepare_turn(conversation, "private", BOB, idempotency_key="one-key")


async def test_concurrent_retries_prepare_exactly_one_message_and_run(
    storage: tuple[Repository, RunRepository],
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    turns = await asyncio.gather(
        *[
            service.prepare_turn(conversation, "one", ALICE, idempotency_key="concurrent")
            for _ in range(8)
        ]
    )
    assert len({turn.message_id for turn in turns}) == 1
    assert len({turn.run_id for turn in turns}) == 1
    assert [m.content for m in await service.history(conversation, ALICE)] == ["one"]
    assert all(turn.history == () for turn in turns)


async def test_receipt_replay_preserves_terminal_run_and_does_not_spend_new_budget(
    storage: tuple[Repository, RunRepository],
) -> None:
    service = service_for(storage, max_tokens=10)
    conversation = await service.create(ALICE)
    first = await service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    await storage[1].transition_if_allowed(first.run_id, RunStatus.COMPLETED)
    await storage[0].record_run_token_usage(conversation, first.run_id, 10)

    replay = await service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    assert replay.run_id == first.run_id
    run = await storage[1].get(first.run_id)
    assert run is not None and run.status == RunStatus.COMPLETED
    with pytest.raises(ConversationTokenBudgetExceeded):
        await service.prepare_turn(conversation, "two", ALICE, idempotency_key="different")
    assert [m.content for m in await service.history(conversation, ALICE)] == ["one"]


@pytest.mark.parametrize("key", ["", "   ", "x" * 513, "ש" * 257])
async def test_invalid_key_writes_nothing(
    storage: tuple[Repository, RunRepository], key: str
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    with pytest.raises(ValueError, match=r"[Ii]dempotency"):
        await service.prepare_turn(conversation, "one", ALICE, idempotency_key=key)
    assert await service.history(conversation, ALICE) == []


async def test_edits_and_idempotency_cannot_be_combined(
    storage: tuple[Repository, RunRepository],
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    turn = await service.prepare_turn(conversation, "one", ALICE)
    with pytest.raises(ValueError, match="edit"):
        await service.prepare_turn(
            conversation,
            "replacement",
            ALICE,
            idempotency_key="edit",
            edit_message_id=turn.message_id,
        )
    assert [m.content for m in await service.history(conversation, ALICE)] == ["one"]


class UnavailableRunRepository(InMemoryRunRepository):
    async def create_if_absent(self, record: RunRecord) -> bool:
        raise OSError("registration unavailable")


async def test_registration_failure_leaves_a_recoverable_prepared_message() -> None:
    repository = MemoryRepository()
    service = service_for((repository, UnavailableRunRepository()))
    conversation = await service.create(ALICE)
    with pytest.raises(OSError, match="registration unavailable"):
        await service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    original = (await repository.list_conversation_messages(conversation))[0]

    restarted = service_for((repository, InMemoryRunRepository()))
    recovered = await restarted.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    assert (recovered.message_id, recovered.run_id) == (original.message_id, original.run_id)
    assert len(await restarted.history(conversation, ALICE)) == 1


async def test_sql_receipt_survives_a_new_process_storage_handle(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    database = create_db_engine(url)
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(database)
    first_service = service_for((SqlRepository(sessions), SqlRunRepository(sessions)))
    conversation = await first_service.create(ALICE)
    original = await first_service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    await database.dispose()

    reopened = create_db_engine(url)
    try:
        sessions = session_factory(reopened)
        restarted = service_for((SqlRepository(sessions), SqlRunRepository(sessions)))
        recovered = await restarted.prepare_turn(
            conversation, "one", ALICE, idempotency_key="event"
        )
        assert (recovered.message_id, recovered.run_id) == (original.message_id, original.run_id)
        assert [m.role for m in await restarted.history(conversation, ALICE)] == [Role.USER]
    finally:
        await reopened.dispose()


async def test_stale_head_rolls_back_receipt_so_the_key_can_be_prepared_again(
    storage: tuple[Repository, RunRepository],
) -> None:
    from datetime import UTC, datetime

    repository = storage[0]
    assert isinstance(repository, IdempotentTurnRepository)
    service = service_for(storage)
    conversation = await service.create(ALICE)
    first = await service.prepare_turn(conversation, "first", ALICE)
    key = preparation_key_digest("retry-after-conflict")
    stale = ConversationMessage(
        "stale",
        conversation,
        Role.USER,
        "second",
        datetime.now(UTC),
        run_id="stale-run",
        user_id=ALICE.user_id,
    )

    assert await repository.append_prepared_message(key, stale, None) is None
    assert await repository.get_prepared_message(key) is None
    recovered = await service.prepare_turn(
        conversation, "second", ALICE, idempotency_key="retry-after-conflict"
    )
    assert recovered.message_id != "stale"
    assert recovered.history == (ChatMessage(ChatRole.USER, "first"),)
    messages = await service.history(conversation, ALICE)
    assert [m.message_id for m in messages] == [first.message_id, recovered.message_id]


async def test_snapshot_failure_rolls_back_message_head_and_receipt(
    storage: tuple[Repository, RunRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = storage[0]
    service = service_for(storage)
    conversation = await service.create(ALICE)

    async def fail_snapshot(*args: object, **kwargs: object) -> None:
        raise OSError("snapshot unavailable")

    def fail_memory_snapshot(*args: object, **kwargs: object) -> None:
        raise OSError("snapshot unavailable")

    if isinstance(repository, SqlRepository):
        original_rebuild = repository._rebuild_snapshot_in_session
        monkeypatch.setattr(repository, "_rebuild_snapshot_in_session", fail_snapshot)
    else:
        assert isinstance(repository, MemoryRepository)
        original_memory_rebuild = repository._build_snapshot
        monkeypatch.setattr(repository, "_build_snapshot", fail_memory_snapshot)
    with pytest.raises(OSError, match="snapshot unavailable"):
        await service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")

    assert isinstance(repository, IdempotentTurnRepository)
    assert await repository.get_prepared_message(preparation_key_digest("event")) is None
    assert await repository.list_conversation_messages(conversation) == []
    session = await repository.get_session(conversation)
    assert session is not None and session.head_message_id is None
    if isinstance(repository, SqlRepository):
        monkeypatch.setattr(repository, "_rebuild_snapshot_in_session", original_rebuild)
    else:
        monkeypatch.setattr(repository, "_build_snapshot", original_memory_rebuild)
    prepared = await service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    assert prepared.history == ()


async def test_missing_optional_capability_fails_before_writing() -> None:
    class LegacyRepository:
        """A third-party adapter implementing only the original repository port."""

        def __init__(self) -> None:
            self.delegate = MemoryRepository()

        def __getattr__(self, name: str) -> object:
            if name in {"get_prepared_message", "append_prepared_message"}:
                raise AttributeError(name)
            return getattr(self.delegate, name)

    service = service_for((cast(Repository, LegacyRepository()), InMemoryRunRepository()))
    conversation = await service.create(ALICE)

    with pytest.raises(RuntimeError, match="does not support idempotent"):
        await service.prepare_turn(conversation, "one", ALICE, idempotency_key="event")
    assert await service.history(conversation, ALICE) == []
    await service.send(conversation, "normal", ALICE)
    assert [m.content for m in await service.history(conversation, ALICE)] == [
        "normal",
        "answer:normal",
    ]


async def test_different_keys_preserve_distinct_identical_messages(
    storage: tuple[Repository, RunRepository],
) -> None:
    service = service_for(storage)
    conversation = await service.create(ALICE)
    first = await service.prepare_turn(conversation, "same", ALICE, idempotency_key="one")
    second = await service.prepare_turn(conversation, "same", ALICE, idempotency_key="two")
    assert first.run_id != second.run_id
    assert [m.content for m in await service.history(conversation, ALICE)] == ["same", "same"]
