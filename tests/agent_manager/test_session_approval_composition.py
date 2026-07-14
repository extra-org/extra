"""Application composition behavior for in-memory session approvals."""

from __future__ import annotations

from agent_engine.approvals.invocation import SessionApprovalKey
from agent_engine.approvals.session_store import InMemorySessionApprovalRepository
from agent_manager.composition import build_session_approval_repository


def test_composition_builds_the_in_memory_adapter() -> None:
    repository = build_session_approval_repository()
    assert isinstance(repository, InMemorySessionApprovalRepository)


async def test_composed_repository_retains_grant_for_its_lifetime() -> None:
    repository = build_session_approval_repository()
    key = SessionApprovalKey(
        system_namespace="system",
        organization_id="org",
        user_id="user",
        session_id="session",
        agent_id="agent",
        tool_identity="local:local:send",
    )

    await repository.allow(key)

    assert await repository.is_allowed(key) is True


async def test_new_application_repository_starts_without_prior_grants() -> None:
    first = build_session_approval_repository()
    second = build_session_approval_repository()
    key = SessionApprovalKey(
        session_id="session",
        agent_id="agent",
        tool_identity="local:local:send",
    )
    await first.allow(key)

    assert await first.is_allowed(key) is True
    assert await second.is_allowed(key) is False
