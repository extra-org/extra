"""Where "allow for this session" permissions live.

The coordinator depends only on the :class:`SessionApprovalStore` protocol
(Dependency Inversion), never on a concrete store. The default in-memory
implementation is process-local and async-safe.

Session lifecycle: a permission is keyed by :class:`SessionApprovalKey`
(session id + agent id + tool identity). Because the default store holds its
state in a plain in-process set, permissions become unreachable when the
process ends — they are intentionally *not* durable. A distributed deployment
that needs cross-process resume can supply another implementation of this same
protocol (e.g. Redis keyed by the same tuple) without touching callers.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from agent_engine.approvals.invocation import SessionApprovalKey


@runtime_checkable
class SessionApprovalStore(Protocol):
    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        """Return True if this tool is already approved for the session."""

    async def allow(self, key: SessionApprovalKey) -> None:
        """Record a session-scoped approval for this tool."""


class InMemorySessionApprovalStore:
    """Process-local, async-safe session-permission store.

    Not shared across processes/pods: permissions are lost on restart. A single
    ``asyncio.Lock`` guards the set; the critical sections are a membership test
    and an insert, so contention is negligible.
    """

    def __init__(self) -> None:
        self._allowed: set[SessionApprovalKey] = set()
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        async with self._lock:
            return key in self._allowed

    async def allow(self, key: SessionApprovalKey) -> None:
        async with self._lock:
            self._allowed.add(key)
