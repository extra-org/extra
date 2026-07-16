from __future__ import annotations

import runpy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.invocation import SessionApprovalScope, ToolInvocation
from agent_engine.approvals.provider import ApprovalRequest
from agent_engine.engine.types import PendingApproval
from agent_manager.domain import Role

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "interactive_mcp_control"
ApprovalConsole = runpy.run_path(str(EXAMPLE / "approval_console.py"))["ApprovalConsole"]
InMemoryHistory = runpy.run_path(str(EXAMPLE / "history.py"))["InMemoryHistory"]
ObservableSessionApprovalRepository = runpy.run_path(str(EXAMPLE / "session_approvals.py"))[
    "ObservableSessionApprovalRepository"
]


class ScriptedApprovalProvider:
    def __init__(self, decisions: list[ApprovalDecision], events: list[str]) -> None:
        self._decisions = iter(decisions)
        self.events = events
        self.requests: list[ApprovalRequest] = []

    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        decision = next(self._decisions)
        self.events.append(
            f"approval_prompt tool={request.invocation.tool_identity} decision={decision.value}"
        )
        return decision


class McpApprovalHarness:
    def __init__(self, decisions: list[ApprovalDecision]) -> None:
        self.events: list[str] = []
        self.repository = ObservableSessionApprovalRepository(emit=self.events.append)
        self.provider = ScriptedApprovalProvider(decisions, self.events)
        self.coordinator = ApprovalCoordinator(
            self.provider,
            session_repository=self.repository,
        )
        self.executions: list[tuple[str, str]] = []
        self._call_number = 0

    async def invoke(
        self,
        *,
        session_id: str,
        tool_name: str = "lookup",
        arguments: Mapping[str, Any] | None = None,
    ) -> None:
        self._call_number += 1
        outcome = await self.coordinator.resolve(
            ToolInvocation(
                tool_call_id=f"call-{self._call_number}",
                agent_id="mcp_agent",
                tool_name=tool_name,
                session_id=session_id,
                provider="mcp",
                server_id="test-mcp",
                arguments=arguments or {},
                system_namespace="Interactive MCP Control",
                user_id="interactive-user",
            ),
            auto_mode=False,
        )
        if outcome.execute:
            self.events.append(f"tool_executed session={session_id} tool={tool_name}")
            self.executions.append((session_id, tool_name))


async def test_console_reprompts_and_redacts_without_mutating_arguments(capsys) -> None:
    answers = iter(["invalid", "session"])
    arguments = {"query": "FastAPI", "authorization": "Bearer secret"}
    console = ApprovalConsole(read_line=lambda _prompt: next(answers))
    pending = PendingApproval(
        run_id="run",
        approval_id="approval",
        agent_id="agent",
        tool_name="query-docs",
        description="query docs",
        provider="mcp",
        server_id="context7",
        arguments=arguments,
    )

    decision = await console.decide(pending)

    assert decision == ApprovalDecision.ALLOW_FOR_SESSION
    assert arguments["authorization"] == "Bearer secret"
    output = capsys.readouterr().out
    assert "Bearer secret" not in output
    assert "***redacted***" in output
    assert "Invalid decision" in output


def test_history_is_scoped_and_clear_removes_only_selected_session() -> None:
    history = InMemoryHistory()
    history.append("one", Role.USER, "first question")
    history.append("one", Role.ASSISTANT, "first answer")
    history.record_tools("one", ["context7.query-docs status=succeeded"])
    history.append("two", Role.USER, "other session")

    prompt = history.prompt("one", "follow up")
    assert "first question" in prompt
    assert "first answer" in prompt
    assert history.tool_events("one") == ("context7.query-docs status=succeeded",)

    history.clear("one")
    assert history.list("one") == ()
    assert history.tool_events("one") == ()
    assert len(history.list("two")) == 1


async def test_first_mcp_call_requires_approval_and_saves_before_execution() -> None:
    harness = McpApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION])

    await harness.invoke(session_id="session-one")

    assert len(harness.provider.requests) == 1
    assert harness.executions == [("session-one", "lookup")]
    saved_index = next(
        index for index, event in enumerate(harness.events) if event.startswith("[APPROVAL STORED]")
    )
    executed_index = next(
        index for index, event in enumerate(harness.events) if event.startswith("tool_executed")
    )
    assert saved_index < executed_index


async def test_second_same_mcp_tool_uses_session_cache_without_prompt() -> None:
    harness = McpApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION])

    await harness.invoke(session_id="session-one")
    await harness.invoke(session_id="session-one", arguments={"different": "safe-value"})

    assert len(harness.provider.requests) == 1
    assert harness.executions == [
        ("session-one", "lookup"),
        ("session-one", "lookup"),
    ]
    assert any("source=session_cache hit=true" in event for event in harness.events)


async def test_mcp_session_approval_isolated_by_session_id() -> None:
    harness = McpApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION, ApprovalDecision.ALLOW_ONCE])

    await harness.invoke(session_id="session-one")
    await harness.invoke(session_id="session-two")

    assert len(harness.provider.requests) == 2
    assert harness.executions == [
        ("session-one", "lookup"),
        ("session-two", "lookup"),
    ]


async def test_clearing_a_session_removes_its_saved_approval() -> None:
    harness = McpApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION, ApprovalDecision.ALLOW_ONCE])
    scope = SessionApprovalScope(
        session_id="session-one",
        system_namespace="Interactive MCP Control",
        user_id="interactive-user",
    )

    await harness.invoke(session_id="session-one")
    await harness.repository.clear_session(scope)
    await harness.invoke(session_id="session-one")

    assert len(harness.provider.requests) == 2
    assert harness.executions == [
        ("session-one", "lookup"),
        ("session-one", "lookup"),
    ]


async def test_mcp_session_approval_isolated_by_tool_name() -> None:
    harness = McpApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION, ApprovalDecision.ALLOW_ONCE])

    await harness.invoke(session_id="session-one", tool_name="lookup")
    await harness.invoke(session_id="session-one", tool_name="summarize")

    assert len(harness.provider.requests) == 2
    assert harness.executions == [
        ("session-one", "lookup"),
        ("session-one", "summarize"),
    ]


async def test_denied_mcp_tool_never_executes() -> None:
    harness = McpApprovalHarness([ApprovalDecision.DENY])

    await harness.invoke(session_id="session-deny")

    assert len(harness.provider.requests) == 1
    assert harness.executions == []
    assert not any(event.startswith("[APPROVAL STORED]") for event in harness.events)


async def test_session_approval_events_never_log_tool_arguments() -> None:
    harness = McpApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION])

    await harness.invoke(
        session_id="session-secret",
        arguments={"authorization": "Bearer do-not-log", "api_key": "also-secret"},
    )

    output = "\n".join(harness.events)
    assert "do-not-log" not in output
    assert "also-secret" not in output
    assert "authorization" not in output
    assert "api_key" not in output
