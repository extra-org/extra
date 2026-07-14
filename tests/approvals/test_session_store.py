"""In-memory session-approval store semantics and concurrency safety."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from agent_engine.approvals.invocation import (
    SessionApprovalGrant,
    SessionApprovalKey,
    SessionApprovalScope,
)
from agent_engine.approvals.session_store import InMemorySessionApprovalStore


def _key(
    session: str = "s",
    agent: str = "a",
    tool: str = "local:local:send",
    *,
    namespace: str = "system",
    user: str = "user",
    organization: str = "org",
) -> SessionApprovalKey:
    return SessionApprovalKey(
        session_id=session,
        agent_id=agent,
        tool_identity=tool,
        system_namespace=namespace,
        user_id=user,
        organization_id=organization,
    )


async def test_unknown_key_not_allowed() -> None:
    store = InMemorySessionApprovalStore()
    assert await store.is_allowed(_key()) is False


async def test_allow_then_is_allowed() -> None:
    store = InMemorySessionApprovalStore()
    await store.allow(_key())
    assert await store.is_allowed(_key()) is True


async def test_scoping_by_session_agent_and_tool() -> None:
    store = InMemorySessionApprovalStore()
    await store.allow(_key(session="s1", agent="a1", tool="local:local:send"))
    assert await store.is_allowed(_key(session="s2", agent="a1", tool="local:local:send")) is False
    assert await store.is_allowed(_key(session="s1", agent="a2", tool="local:local:send")) is False
    assert await store.is_allowed(_key(session="s1", agent="a1", tool="local:local:other")) is False


async def test_scoping_by_namespace_user_and_organization() -> None:
    store = InMemorySessionApprovalStore()
    await store.allow(_key())
    assert await store.is_allowed(_key(namespace="other")) is False
    assert await store.is_allowed(_key(user="other")) is False
    assert await store.is_allowed(_key(organization="other")) is False


async def test_revoke_and_clear_exact_session() -> None:
    store = InMemorySessionApprovalStore()
    first = _key(tool="local:local:first")
    second = _key(tool="local:local:second")
    other_user = _key(tool="local:local:first", user="other")
    await store.allow(first)
    await store.allow(second)
    await store.allow(other_user)

    await store.revoke(first)
    assert await store.is_allowed(first) is False
    assert await store.is_allowed(second) is True

    await store.clear_session(second.scope)
    assert await store.is_allowed(second) is False
    assert await store.is_allowed(other_user) is True


async def test_expired_grant_is_not_allowed() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    store = InMemorySessionApprovalStore(clock=lambda: now)
    await store.allow(
        _key(),
        grant=SessionApprovalGrant(expires_at=now - timedelta(seconds=1)),
    )
    assert await store.is_allowed(_key()) is False


async def test_clear_unknown_session_is_idempotent() -> None:
    store = InMemorySessionApprovalStore()
    await store.clear_session(
        SessionApprovalScope(
            session_id="missing",
            system_namespace="system",
            user_id="user",
            organization_id="org",
        )
    )


async def test_concurrent_allows_do_not_corrupt_state() -> None:
    store = InMemorySessionApprovalStore()
    keys = [_key(session=f"s{i}") for i in range(50)]
    await asyncio.gather(*(store.allow(k) for k in keys))
    results = await asyncio.gather(*(store.is_allowed(k) for k in keys))
    assert all(results)
