"""One contract, run against every Repository implementation.

If a backend diverges from the port's expected behavior, these fail — which is
the proof that swapping SQLite/Postgres/in-memory is safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import SQLModel, select

import agent_manager.infrastructure.persistence.tables  # noqa: F401  (register tables)
from agent_manager.domain import ConversationMessage, Repository, Role
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from agent_manager.infrastructure.persistence.tables import (
    ConversationMessageRow,
    ConversationSnapshotRow,
)


async def _memory() -> Repository:
    return MemoryRepository()


async def _sql() -> Repository:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return SqlRepository(session_factory(engine))


@pytest.fixture(params=[_memory, _sql], ids=["memory", "sql"])
async def repo(request: pytest.FixtureRequest) -> Repository:
    return await request.param()


async def test_create_and_exists(repo: Repository) -> None:
    cid = await repo.create_conversation()
    assert await repo.conversation_exists(cid)
    assert not await repo.conversation_exists("nope")


async def test_messages_in_insertion_order(repo: Repository) -> None:
    cid = await repo.create_conversation()
    await repo.add_message(cid, Role.USER, "hi")
    await repo.add_message(cid, Role.ASSISTANT, "hello")
    await repo.add_message(cid, Role.USER, "bye")
    msgs = await repo.list_messages(cid)
    assert [(m.role, m.content) for m in msgs] == [
        (Role.USER, "hi"),
        (Role.ASSISTANT, "hello"),
        (Role.USER, "bye"),
    ]


async def test_limit_returns_most_recent_oldest_first(repo: Repository) -> None:
    cid = await repo.create_conversation()
    for i in range(5):
        await repo.add_message(cid, Role.USER, f"m{i}")
    assert [m.content for m in await repo.list_messages(cid, limit=2)] == ["m3", "m4"]


async def test_same_turn_keeps_order(repo: Repository) -> None:
    cid = await repo.create_conversation()
    await repo.add_message(cid, Role.USER, "q")
    await repo.add_message(cid, Role.ASSISTANT, "a")
    assert [m.role for m in await repo.list_messages(cid)] == [Role.USER, Role.ASSISTANT]


async def test_isolated_per_conversation(repo: Repository) -> None:
    a = await repo.create_conversation()
    b = await repo.create_conversation()
    await repo.add_message(a, Role.USER, "in-a")
    assert await repo.list_messages(b) == []


async def test_create_and_get_user(repo: Repository) -> None:
    user = await repo.upsert_user(
        "u1",
        external_user_id="ext-1",
        username="alice",
        display_name="Alice",
        metadata={"tier": "pro"},
    )

    fetched = await repo.get_user("u1")

    assert fetched == user
    assert fetched is not None
    assert fetched.metadata == {"tier": "pro"}


async def test_create_and_get_session(repo: Repository) -> None:
    await repo.upsert_user("u1")

    session = await repo.create_session(
        "sess-1",
        user_id="u1",
        system_name="system",
        config_path="/tmp/agents.yml",
        title="Support",
        metadata={"source": "test"},
    )

    fetched = await repo.get_session("sess-1")
    assert fetched == session
    assert fetched is not None
    assert fetched.user_id == "u1"
    assert fetched.system_name == "system"


async def test_append_message_updates_cold_and_hot_snapshot(repo: Repository) -> None:
    await repo.create_session("sess-1", user_id="u1")
    message = _message("sess-1", Role.USER, "hello", run_id="run-1", user_id="u1")

    snapshot = await repo.append_message(message, snapshot_ttl_seconds=60)

    cold = await repo.list_conversation_messages("sess-1")
    assert [m.message_id for m in cold] == [message.message_id]
    assert snapshot.session_id == "sess-1"
    assert snapshot.user_id == "u1"
    assert snapshot.message_count == 1
    assert snapshot.last_message_id == message.message_id
    assert snapshot.expires_at is not None


async def test_retrieve_conversation_context_with_bounds(repo: Repository) -> None:
    await repo.create_session("sess-1")
    for i in range(5):
        await repo.append_message(_message("sess-1", Role.USER, f"message-{i}", run_id=f"run-{i}"))

    context = await repo.get_context("sess-1", max_messages=2, max_chars=100)

    assert context.session_id == "sess-1"
    assert context.message_count == 5
    assert [m.content for m in context.messages] == ["message-3", "message-4"]


async def test_delete_expired_snapshots_and_rebuild(repo: Repository) -> None:
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "hello"), snapshot_ttl_seconds=1)
    deleted = await repo.delete_expired_snapshots(datetime.now(UTC) + timedelta(seconds=2))

    assert deleted == 1
    assert await repo.get_snapshot("sess-1") is None

    rebuilt = await repo.rebuild_snapshot("sess-1")
    assert rebuilt is not None
    assert rebuilt.message_count == 1


async def test_same_session_multiple_appends_one_snapshot_row(repo: Repository) -> None:
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "a", run_id="run-1"))
    await repo.append_message(_message("sess-1", Role.ASSISTANT, "b", run_id="run-2"))

    snapshot = await repo.get_snapshot("sess-1")

    assert snapshot is not None
    assert snapshot.message_count == 2
    assert snapshot.session_id == "sess-1"


async def test_different_run_ids_can_share_one_session(repo: Repository) -> None:
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "a", run_id="run-1"))
    await repo.append_message(_message("sess-1", Role.USER, "b", run_id="run-2"))

    messages = await repo.list_conversation_messages("sess-1")

    assert [m.run_id for m in messages] == ["run-1", "run-2"]


async def test_sql_schema_has_cold_and_snapshot_rows() -> None:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(engine)
    repo = SqlRepository(sessions)
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "hello"))

    async with sessions() as session:
        cold = (await session.exec(select(ConversationMessageRow))).all()
        snapshots = (await session.exec(select(ConversationSnapshotRow))).all()

    assert len(cold) == 1
    assert len(snapshots) == 1


def _message(
    session_id: str,
    role: Role,
    content: str,
    *,
    run_id: str | None = None,
    user_id: str | None = None,
) -> ConversationMessage:
    return ConversationMessage(
        message_id=uuid4().hex,
        session_id=session_id,
        run_id=run_id,
        user_id=user_id,
        role=role,
        content=content,
        created_at=datetime.now(UTC),
    )
