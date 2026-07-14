"""ApprovalCoordinator: the deterministic auto -> session -> ask workflow.

Covers the standard, session, denial, auto-mode, failure, security, and
concurrency scenarios through the public ``resolve`` API using a fake provider
and the real in-memory session store.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.invocation import SessionApprovalKey, ToolInvocation
from agent_engine.approvals.provider import ApprovalRequest
from agent_engine.approvals.session_store import InMemorySessionApprovalStore


class RecordingProvider:
    """Returns a fixed decision (or raises) and records the requests it saw."""

    def __init__(
        self, decision: ApprovalDecision | None = None, *, raises: Exception | None = None
    ) -> None:
        self._decision = decision
        self._raises = raises
        self.requests: list[ApprovalRequest] = []

    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        if self._raises is not None:
            raise self._raises
        assert self._decision is not None
        return self._decision


class MappingProvider:
    """Returns a per-invocation decision, yielding so calls interleave."""

    def __init__(self, by_call: dict[str, ApprovalDecision]) -> None:
        self._by_call = by_call
        self.seen: list[str] = []

    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        self.seen.append(request.invocation.tool_call_id)
        await asyncio.sleep(0)
        return self._by_call[request.invocation.tool_call_id]


class LegacySessionApprovalStore:
    """An integration implementing the original two-method store contract."""

    def __init__(self) -> None:
        self.allowed: set[SessionApprovalKey] = set()

    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        return key in self.allowed

    async def allow(self, key: SessionApprovalKey) -> None:
        self.allowed.add(key)


def _inv(
    *,
    tool_call_id: str = "tc1",
    agent_id: str = "a",
    tool_name: str = "send_email",
    session_id: str | None = "conv1",
    server_id: str | None = None,
    provider: str = "local",
    arguments: dict | None = None,
) -> ToolInvocation:
    return ToolInvocation(
        tool_call_id=tool_call_id,
        agent_id=agent_id,
        tool_name=tool_name,
        session_id=session_id,
        provider=provider,  # type: ignore[arg-type]
        server_id=server_id,
        arguments=arguments or {},
    )


# ------------------------------- standard --------------------------------- #


async def test_allow_once_executes_and_asks_again() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_ONCE)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())

    first = await coord.resolve(_inv(), auto_mode=False)
    assert first.execute is True
    assert first.decision == ApprovalDecision.ALLOW_ONCE

    second = await coord.resolve(
        _inv(tool_call_id="tc2", arguments={"different": "arguments"}),
        auto_mode=False,
    )
    assert second.execute is True
    # ALLOW_ONCE persists nothing: the provider is consulted a second time.
    assert len(provider.requests) == 2


# ------------------------------- session ---------------------------------- #


async def test_allow_for_session_suppresses_further_prompts() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_FOR_SESSION)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())

    assert (await coord.resolve(_inv(), auto_mode=False)).execute is True
    # Same tool, agent, session -> no second prompt.
    second = await coord.resolve(
        _inv(tool_call_id="tc2", arguments={"different": "arguments"}),
        auto_mode=False,
    )
    assert second.execute is True
    assert second.decision is None  # session-allowed, provider not consulted
    assert len(provider.requests) == 1


async def test_legacy_session_store_keeps_original_allow_signature() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_FOR_SESSION)
    store = LegacySessionApprovalStore()
    coord = ApprovalCoordinator(provider, session_store=store)

    assert (await coord.resolve(_inv(), auto_mode=False)).execute is True
    second = await coord.resolve(_inv(tool_call_id="tc2"), auto_mode=False)

    assert second.execute is True
    assert second.decision is None
    assert len(provider.requests) == 1


async def test_session_scope_is_by_session_agent_and_tool() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_FOR_SESSION)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    await coord.resolve(_inv(), auto_mode=False)  # grants for (conv1, a, send_email)

    # Different session, agent, or tool -> approval required again.
    assert (await coord.resolve(_inv(session_id="conv2"), auto_mode=False)).decision is not None
    assert (await coord.resolve(_inv(agent_id="b"), auto_mode=False)).decision is not None
    assert (await coord.resolve(_inv(tool_name="other"), auto_mode=False)).decision is not None


async def test_same_short_name_different_provider_not_shared() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_FOR_SESSION)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    await coord.resolve(_inv(provider="mcp", server_id="srvA"), auto_mode=False)
    # Same short name on a different server is a different identity.
    out = await coord.resolve(_inv(provider="mcp", server_id="srvB"), auto_mode=False)
    assert out.decision is not None  # approval was requested again


# ------------------------------- denial ----------------------------------- #


async def test_deny_does_not_execute_or_store() -> None:
    provider = RecordingProvider(ApprovalDecision.DENY)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    out = await coord.resolve(_inv(), auto_mode=False)
    assert out.execute is False
    # Nothing was stored: a second call still asks.
    await coord.resolve(_inv(tool_call_id="tc2"), auto_mode=False)
    assert len(provider.requests) == 2


# ------------------------------- auto mode -------------------------------- #


async def test_auto_mode_bypasses_provider() -> None:
    provider = RecordingProvider(ApprovalDecision.DENY)  # would deny if consulted
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    out = await coord.resolve(_inv(), auto_mode=True)
    assert out.execute is True
    assert out.decision is None
    assert provider.requests == []


async def test_auto_mode_is_per_invocation() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_ONCE)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    # Agent A auto -> executes silently; agent B not auto -> still asks.
    assert (await coord.resolve(_inv(agent_id="A"), auto_mode=True)).decision is None
    assert (await coord.resolve(_inv(agent_id="B"), auto_mode=False)).decision is not None


# ------------------------------- failure cases ---------------------------- #


async def test_provider_exception_prevents_execution() -> None:
    provider = RecordingProvider(raises=RuntimeError("provider down"))
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    with pytest.raises(RuntimeError):
        await coord.resolve(_inv(), auto_mode=False)


async def test_missing_session_cannot_be_granted_for_session() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_FOR_SESSION)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    # No session id -> no key -> permission cannot be stored; still executes once...
    assert (await coord.resolve(_inv(session_id=None), auto_mode=False)).execute is True
    # ...but the next call must ask again (fail closed).
    await coord.resolve(_inv(session_id=None, tool_call_id="tc2"), auto_mode=False)
    assert len(provider.requests) == 2


# ------------------------------- security --------------------------------- #


async def test_request_masks_secrets_without_mutating_arguments() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_ONCE)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    original = {"to": "a@b.com", "api_key": "sk-secret"}
    await coord.resolve(_inv(arguments=original), auto_mode=False)

    seen = provider.requests[0]
    assert seen.masked_arguments["api_key"] == "***redacted***"
    assert seen.masked_arguments["to"] == "a@b.com"
    assert "not been executed" in seen.description.lower()
    # The original arguments handed to the tool are untouched.
    assert original["api_key"] == "sk-secret"


# ------------------------------- concurrency ------------------------------ #


async def test_concurrent_invocations_match_their_own_decisions() -> None:
    provider = MappingProvider({"tc1": ApprovalDecision.ALLOW_ONCE, "tc2": ApprovalDecision.DENY})
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    out1, out2 = await asyncio.gather(
        coord.resolve(_inv(tool_call_id="tc1"), auto_mode=False),
        coord.resolve(_inv(tool_call_id="tc2"), auto_mode=False),
    )
    assert out1.execute is True and out1.decision == ApprovalDecision.ALLOW_ONCE
    assert out2.execute is False and out2.decision == ApprovalDecision.DENY


async def test_concurrent_session_grants_do_not_corrupt_state() -> None:
    provider = RecordingProvider(ApprovalDecision.ALLOW_FOR_SESSION)
    coord = ApprovalCoordinator(provider, session_store=InMemorySessionApprovalStore())
    outs = await asyncio.gather(
        *(coord.resolve(_inv(tool_call_id=f"tc{i}"), auto_mode=False) for i in range(10))
    )
    assert all(o.execute for o in outs)
    # After any grant lands, the tool is allowed for the session.
    assert (await coord.resolve(_inv(tool_call_id="final"), auto_mode=False)).decision is None
