"""Exclusive connection supervisor: local OS lock or dedicated PostgreSQL session."""

from __future__ import annotations

import asyncio
import errno
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from agent_manager.domain.channels.models import stable_id
from agent_manager.domain.channels.ownership import (
    ChannelWorkerAlreadyRunning,
    ChannelWorkerOwnership,
    ChannelWorkerOwnershipLost,
)


class SqlChannelWorkerOwnership(ChannelWorkerOwnership):
    def __init__(self, database_url: str, connection_id: str) -> None:
        self._url = database_url
        self._scope = stable_id("channel-worker", connection_id)
        self._entered = False
        self._held = False
        self._lost = False
        self._connection: AsyncConnection | None = None
        self._backend_pid: int | None = None

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        if self._entered:
            raise ChannelWorkerAlreadyRunning("channel supervisor already entered")
        self._entered = True
        self._lost = False
        try:
            backend = make_url(self._url).get_backend_name()
            if backend == "sqlite":
                async with self._sqlite():
                    yield
            elif backend == "postgresql":
                async with self._postgres():
                    yield
            else:
                raise ValueError("unsupported channel ownership backend")
        finally:
            self._entered = False
            self._held = False

    @asynccontextmanager
    async def _sqlite(self) -> AsyncIterator[None]:
        try:
            import fcntl
        except ImportError:
            raise ValueError("SQLite channel ownership requires local POSIX storage") from None
        database = make_url(self._url).database or ""
        if database.startswith("file:"):
            database = unquote(urlsplit(database).path)
        path = Path(database).resolve()
        lock_path = path.with_name(f".{path.name}.extra-worker-{self._scope[:16]}.lock")
        with lock_path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                raise ChannelWorkerAlreadyRunning(
                    "another worker owns this channel connection"
                ) from None
            self._held = True
            try:
                yield
            finally:
                self._held = False
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        # Do not unlink: another process may already be waiting on this inode.

    @asynccontextmanager
    async def _postgres(self) -> AsyncIterator[None]:
        engine = create_async_engine(self._url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        key = int.from_bytes(bytes.fromhex(self._scope)[:8], "big", signed=True)
        try:
            async with engine.connect() as connection:
                acquired = (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                    )
                ).scalar_one()
                if not acquired:
                    raise ChannelWorkerAlreadyRunning("another worker owns this channel connection")
                self._connection = connection
                self._backend_pid = (
                    await connection.execute(text("SELECT pg_backend_pid()"))
                ).scalar_one()
                self._held = True
                try:
                    yield
                finally:
                    self._held = False
                    self._connection = None
                    if not self._lost:
                        with suppress(Exception):
                            await connection.execute(
                                text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                            )
        finally:
            await engine.dispose()

    async def check(self) -> None:
        if not self._held or self._lost:
            raise ChannelWorkerOwnershipLost("channel supervisor ownership is unavailable")
        if self._connection is not None:
            try:
                async with asyncio.timeout(5):
                    pid = (
                        await self._connection.execute(text("SELECT pg_backend_pid()"))
                    ).scalar_one()
                if pid != self._backend_pid:
                    raise ChannelWorkerOwnershipLost("channel ownership session was replaced")
            except Exception:
                self._lost = True
                raise ChannelWorkerOwnershipLost("channel supervisor ownership was lost") from None
