"""One contract, run against every Repository implementation.

If a backend diverges from the port's expected behavior, these fail — which is
the proof that swapping SQLite/Postgres/in-memory is safe.
"""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel

import agent_manager.infrastructure.persistence.tables  # noqa: F401  (register tables)
from agent_manager.domain import Repository, Role
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository


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
