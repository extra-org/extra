"""Exclusive local ownership and dedicated PostgreSQL-session contract."""

from pathlib import Path

import pytest

from agent_manager.domain.channels.ownership import (
    ChannelWorkerAlreadyRunning,
    ChannelWorkerOwnershipLost,
)


async def test_sqlite_connection_has_one_owner_and_releases_after_an_exception(tmp_path: Path):
    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}"
    first = SqlChannelWorkerOwnership(url, "connection")
    second = SqlChannelWorkerOwnership(url, "connection")
    with pytest.raises(RuntimeError, match="injected"):
        async with first.hold():
            await first.check()
            with pytest.raises(ChannelWorkerAlreadyRunning):
                async with second.hold():
                    pytest.fail("second owner entered the same connection")
            async with SqlChannelWorkerOwnership(url, "other").hold():
                pass
            raise RuntimeError("injected")
    with pytest.raises(ChannelWorkerOwnershipLost):
        await first.check()
    async with second.hold():
        await second.check()


async def test_sqlite_uri_and_path_aliases_share_one_lock(tmp_path: Path):
    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    database = tmp_path / "queue.db"
    url = f"sqlite+aiosqlite:///{database}"
    alias = f"sqlite+aiosqlite:///file:{database}?uri=true"
    async with SqlChannelWorkerOwnership(url, "connection").hold():
        with pytest.raises(ChannelWorkerAlreadyRunning):
            async with SqlChannelWorkerOwnership(alias, "connection").hold():
                pytest.fail("URI alias bypassed connection ownership")


class PgResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class PgSession:
    def __init__(self):
        self.pid = 100
        self.locked = False
        self.unlocks = 0
        self.closed = False

    async def __aenter__(self):
        self.closed = False
        return self

    async def __aexit__(self, *args):
        self.closed = True
        self.locked = False

    async def execute(self, statement, parameters=None):
        query = str(statement)
        if "pg_try_advisory_lock" in query:
            granted = not self.locked
            self.locked = True
            return PgResult(granted)
        if "pg_advisory_unlock" in query:
            self.locked = False
            self.unlocks += 1
            return PgResult(True)
        if "pg_backend_pid" in query:
            return PgResult(self.pid)
        raise AssertionError(f"unexpected ownership SQL: {query}")


class PgEngine:
    def __init__(self, session):
        self.session = session
        self.disposed = False

    def connect(self):
        return self.session

    async def dispose(self):
        self.disposed = True


async def test_postgres_ownership_detects_session_replacement_and_closes_its_connection(
    monkeypatch,
):
    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    session = PgSession()
    engine = PgEngine(session)
    monkeypatch.setattr(
        "agent_manager.infrastructure.persistence.channels.ownership.create_async_engine",
        lambda *a, **kw: engine,
    )
    owner = SqlChannelWorkerOwnership("postgresql+asyncpg://unused.invalid/test", "connection")
    async with owner.hold():
        await owner.check()
        session.pid = 101
        with pytest.raises(ChannelWorkerOwnershipLost):
            await owner.check()
    assert session.closed and engine.disposed


async def test_postgres_busy_lock_fails_without_polling_or_stealing(monkeypatch):
    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    session = PgSession()
    session.locked = True
    engine = PgEngine(session)
    monkeypatch.setattr(
        "agent_manager.infrastructure.persistence.channels.ownership.create_async_engine",
        lambda *a, **kw: engine,
    )
    with pytest.raises(ChannelWorkerAlreadyRunning):
        async with SqlChannelWorkerOwnership(
            "postgresql+asyncpg://unused.invalid/test", "connection"
        ).hold():
            pytest.fail("busy PostgreSQL lock was acquired")
    assert session.unlocks == 0 and engine.disposed


async def test_posix_permission_busy_errno_is_reported_as_another_owner(tmp_path, monkeypatch):
    import errno
    import fcntl

    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    def busy(*args):
        raise OSError(errno.EACCES, "injected busy lock")

    monkeypatch.setattr(fcntl, "flock", busy)
    with pytest.raises(ChannelWorkerAlreadyRunning):
        async with SqlChannelWorkerOwnership(
            f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}", "connection"
        ).hold():
            pytest.fail("busy lock entered")
