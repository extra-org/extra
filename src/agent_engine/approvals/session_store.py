"""Session-approval repository port and local in-memory adapter.

The engine depends only on :class:`SessionApprovalRepository`. Persistent
implementations live outside ``agent_engine`` and are injected by application
composition. The in-memory implementation remains here as the dependency-free
local-development and backwards-compatibility adapter.

Session lifecycle: a permission is keyed by :class:`SessionApprovalKey`, which
includes deployment, tenant, user, session, agent, and provider-qualified tool
identity. The local adapter holds state for the lifetime of the injected
repository instance. A distributed deployment supplies a persistent adapter
behind the same protocol without changing engine callers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from agent_engine.approvals.invocation import (
    SessionApprovalGrant,
    SessionApprovalKey,
    SessionApprovalScope,
)


@runtime_checkable
class SessionApprovalStore(Protocol):
    """Legacy session-approval contract retained for existing integrations."""

    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        """Return True if this tool is already approved for the session."""

    async def allow(self, key: SessionApprovalKey) -> None:
        """Record a session-scoped approval for this tool."""


@runtime_checkable
class SessionApprovalRepository(Protocol):
    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        """Return True if this tool is already approved for the session."""

    async def allow(
        self, key: SessionApprovalKey, *, grant: SessionApprovalGrant | None = None
    ) -> None:
        """Record a session-scoped approval for this tool."""

    async def revoke(self, key: SessionApprovalKey) -> None:
        """Revoke one permission if it exists."""

    async def clear_session(self, scope: SessionApprovalScope) -> None:
        """Revoke every permission in the exact session identity scope."""


class InMemorySessionApprovalRepository(SessionApprovalRepository):
    """Process-local, async-safe session-permission store.

    Not shared across processes/pods: permissions are lost on restart. A single
    ``asyncio.Lock`` guards the set; the critical sections are a membership test
    and an insert, so contention is negligible.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._grants: dict[SessionApprovalKey, SessionApprovalGrant] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        async with self._lock:
            grant = self._grants.get(key)
            if grant is None:
                return False
            if grant.expires_at is not None and grant.expires_at <= self._clock():
                self._grants.pop(key, None)
                return False
            return True

    async def allow(
        self, key: SessionApprovalKey, *, grant: SessionApprovalGrant | None = None
    ) -> None:
        async with self._lock:
            self._grants[key] = grant or SessionApprovalGrant()

    async def revoke(self, key: SessionApprovalKey) -> None:
        async with self._lock:
            self._grants.pop(key, None)

    async def clear_session(self, scope: SessionApprovalScope) -> None:
        async with self._lock:
            matching = [key for key in self._grants if key.scope == scope]
            for key in matching:
                self._grants.pop(key, None)


# Backwards-compatible concrete name for integrations built against the first HITL API.
InMemorySessionApprovalStore = InMemorySessionApprovalRepository
