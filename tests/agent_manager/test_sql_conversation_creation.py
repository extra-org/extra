"""Creating conversations against a real SQL database.

The contract tests cover the ownership rules on both backends; these cover what
only a second connection can show — the window between reading "no such id" and
inserting it, and what a rejected create leaves behind. SQLite on disk, so the
racers get separate connections: an in-memory database is one shared connection
and races nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import agent_manager.infrastructure.persistence.tables  # noqa: F401  (register tables)
from agent_manager.application import ConversationService
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from tests.agent_manager.conftest import RecordingEngine, bearer, build_test_app

RepositoryFactory = Callable[[], SqlRepository]


@pytest.fixture
async def db_url(tmp_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{tmp_path / 'race.db'}"
    engine = create_db_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    finally:
        await engine.dispose()
    return url


@pytest.fixture
async def repository_factory(db_url: str) -> AsyncIterator[RepositoryFactory]:
    """Separate engines for real races, all owned by this test's lifetime."""
    engines: list[AsyncEngine] = []

    def create() -> SqlRepository:
        engine = create_db_engine(db_url)
        engines.append(engine)
        return SqlRepository(session_factory(engine))

    try:
        yield create
    finally:
        for engine in engines:
            await engine.dispose()


async def test_concurrent_create_session_settles_on_one_owner(
    repository_factory: RepositoryFactory,
) -> None:
    alice, bob = repository_factory(), repository_factory()

    results = await asyncio.gather(
        alice.create_session("shared-id", user_id="alice"),
        bob.create_session("shared-id", user_id="bob"),
        return_exceptions=True,
    )

    # The loser's insert hits the primary key; it must come back with the
    # winner's session rather than a raw IntegrityError.
    assert [type(r).__name__ for r in results] == ["ConversationSession", "ConversationSession"]
    owners = {r.user_id for r in results}  # type: ignore[union-attr]
    assert len(owners) == 1
    stored = await alice.get_session("shared-id")
    assert stored is not None
    assert stored.user_id in {"alice", "bob"}
    assert {stored.user_id} == owners


async def test_an_integrity_error_that_is_not_a_duplicate_id_is_not_swallowed(
    repository_factory: RepositoryFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a taken id is absorbed. Reading back nothing means the insert failed
    for another reason — a foreign key, a not-null — and that belongs upstream."""
    repo = repository_factory()
    await repo.create_session("shared-id", user_id="alice")

    # Blind the reads, so the insert both collides and finds no winner.
    async def blind(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(AsyncSession, "get", blind)

    with pytest.raises(IntegrityError):
        await repo.create_session("shared-id", user_id="bob")


async def test_the_api_answers_409_to_the_losing_caller(
    repository_factory: RepositoryFactory,
) -> None:
    """Two app instances on one database, the way two replicas would be — a
    single app serialises the requests and never reaches the race."""

    def client() -> AsyncClient:
        app = build_test_app(ConversationService(RecordingEngine(), repository_factory()))
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def create(client: AsyncClient, user: str) -> Coroutine[Any, Any, Response]:
        return client.post(
            "/conversations",
            json={"session_id": "shared-id"},
            headers=bearer(user),
        )

    async with client() as alice, client() as bob:
        responses = await asyncio.gather(create(alice, "alice"), create(bob, "bob"))

    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_a_rejected_create_leaves_the_other_system_s_session_alone(
    repository_factory: RepositoryFactory,
) -> None:
    """Two systems sharing a conversation database, which is when this bites:
    naming a live id must not rebind it to the caller's system."""

    def client(system: str) -> AsyncClient:
        app = build_test_app(
            ConversationService(
                RecordingEngine(),
                repository_factory(),
                system_name=system,
                config_path=f"/{system}/agents.yml",
            )
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async with client("system-a") as alice, client("system-b") as bob:
        created = await alice.post(
            "/conversations",
            json={"session_id": "shared-id"},
            headers=bearer("alice"),
        )
        assert created.status_code == 200
        before = await repository_factory().get_session("shared-id")

        rejected = await bob.post(
            "/conversations",
            json={"session_id": "shared-id"},
            headers=bearer("bob"),
        )

    assert rejected.status_code == 409
    assert await repository_factory().get_session("shared-id") == before
