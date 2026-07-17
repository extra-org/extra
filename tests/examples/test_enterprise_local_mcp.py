"""Offline local-MCP discovery and deterministic approval-scope tests."""

from __future__ import annotations

import os
import runpy
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.invocation import ToolInvocation
from agent_engine.approvals.provider import ApprovalRequest
from agent_engine.approvals.session_store import InMemorySessionApprovalRepository
from agent_engine.core.spec import AgentSpec
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.types import ChatMessage, RunResult
from agent_engine.models.factory import ModelConfigurationError
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks import RunContext
from agent_manager.application import ConversationService
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "enterprise-knowledge-assistant"
RUNNER_PATH = EXAMPLE / "run_local_mcp_approval.py"
SERVER_ID = "local_knowledge_mcp"
RUNNER = runpy.run_path(str(RUNNER_PATH))
TOOLS = runpy.run_path(str(EXAMPLE / "mcps" / "local_knowledge_mcp" / "tools.py"))


class PassiveChatModel:
    """Allows engine construction without selecting a tool or calling a provider."""

    def bind_tools(self, tools: list[Any]) -> PassiveChatModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content="unused")

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield AIMessage(content="unused")


class ContextAwareSearchModel:
    """Deterministic model that requires structured history to resolve a follow-up."""

    def __init__(self, follow_up: str) -> None:
        self.follow_up = follow_up
        self.invocations: list[list[Any]] = []
        self.saw_follow_up_context = False
        self.saw_follow_up_tool_result = False

    def bind_tools(self, tools: list[Any]) -> ContextAwareSearchModel:
        assert "search_internal_documents" in {tool.name for tool in tools}
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.invocations.append(list(messages))
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield await self.ainvoke(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        latest_is_tool_result = isinstance(messages[-1], ToolMessage)

        if latest_user == self.follow_up:
            expected = [
                (SystemMessage, None),
                (
                    HumanMessage,
                    "Find internal documents about the session approval security policy "
                    "and summarize them.",
                ),
                (
                    AIMessage,
                    "I found the session approval policy.\n"
                    "1. Search more broadly using authentication and access-control terms\n"
                    "2. Search another system",
                ),
                (HumanMessage, self.follow_up),
            ]
            for message, (expected_type, expected_content) in zip(
                messages[:4], expected, strict=True
            ):
                assert isinstance(message, expected_type)
                if expected_content is not None:
                    assert message.content == expected_content
            self.saw_follow_up_context = True
            if latest_is_tool_result:
                self.saw_follow_up_tool_result = True
                return AIMessage(
                    content="The broader search found authentication and access-control guidance."
                )
            return AIMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        name="search_internal_documents",
                        args={"query": "authentication access control"},
                        id="follow-up-search",
                    )
                ],
            )

        if latest_is_tool_result:
            return AIMessage(
                content=(
                    "I found the session approval policy.\n"
                    "1. Search more broadly using authentication and access-control terms\n"
                    "2. Search another system"
                )
            )
        return AIMessage(
            content="",
            tool_calls=[
                ToolCall(
                    name="search_internal_documents",
                    args={"query": "session approval security policy"},
                    id="initial-search",
                )
            ],
        )


class CompletedHistoryEngine:
    """Minimal runner seam: records structured history and completes immediately."""

    def __init__(self) -> None:
        self.histories: list[tuple[ChatMessage, ...]] = []

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        del context
        self.histories.append(tuple(history))
        answer = "1. Search more broadly" if message != "1" else "Contextual follow-up"
        return RunResult(system_name="demo", visited=["agent"], answer=answer)


def passive_model_factory(
    provider: str, name: str, temperature: float | None, **_: Any
) -> BaseChatModel:
    del provider, name, temperature
    return cast(BaseChatModel, PassiveChatModel())


async def _complete_conversation_turn(
    engine: LangGraphEngine,
    conversations: ConversationService,
    session_id: str,
    text: str,
    *,
    approve_for_session: bool,
) -> tuple[RunResult, bool]:
    turn = await conversations.prepare_turn(session_id, text, user_id="local-demo-user")
    result = await engine.run(
        turn.message,
        history=turn.history,
        context=RunContext(
            run_id=turn.run_id,
            conversation_id=turn.session_id,
            user_id=turn.user_id,
        ),
    )
    prompted = result.pending_approval is not None
    if result.pending_approval is not None:
        pending = result.pending_approval
        result = await engine.resume(
            turn.run_id,
            pending.approval_id,
            (
                ApprovalDecision.ALLOW_FOR_SESSION
                if approve_for_session
                else ApprovalDecision.ALLOW_ONCE
            ),
            caller_user_id="local-demo-user",
        )
    await conversations.complete_turn(turn, result)
    return result, prompted


class ScriptedApprovalProvider:
    def __init__(self, decisions: list[ApprovalDecision]) -> None:
        self._decisions = iter(decisions)
        self.requests: list[ApprovalRequest] = []

    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return next(self._decisions)


class ApprovalHarness:
    def __init__(self, decisions: list[ApprovalDecision]) -> None:
        self.provider = ScriptedApprovalProvider(decisions)
        self.repository = InMemorySessionApprovalRepository()
        self.coordinator = ApprovalCoordinator(
            self.provider,
            session_repository=self.repository,
        )
        self.executed: list[str] = []
        self._call_number = 0

    async def invoke(
        self,
        *,
        session_id: str,
        tool_name: str,
    ) -> bool:
        self._call_number += 1
        invocation = ToolInvocation(
            tool_call_id=f"call-{self._call_number}",
            agent_id="local_knowledge_agent",
            tool_name=tool_name,
            session_id=session_id,
            provider="mcp",
            server_id=SERVER_ID,
            system_namespace="Enterprise Knowledge Assistant Local MCP Approval",
            user_id="local-demo-user",
        )
        outcome = await self.coordinator.resolve(invocation, auto_mode=False)
        if outcome.execute:
            self.executed.append(invocation.tool_identity)
        return outcome.execute


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Local MCP exited before startup: {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("Timed out waiting for the local MCP server")


@pytest.fixture(scope="module")
def local_mcp_url() -> Iterator[str]:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mcps.local_knowledge_mcp.server",
            "--port",
            str(port),
        ],
        cwd=EXAMPLE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_server(port, process)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def test_local_mcp_is_discovered_through_engine_flow(
    local_mcp_url: str,
) -> None:
    load_spec = RUNNER["load_spec"]
    spec = load_spec(mcp_url=local_mcp_url)
    async with LangGraphEngine(
        EXAMPLE,
        model_factory=passive_model_factory,
    ) as engine:
        await engine.build(spec)
        discovered = engine.discovered_mcp_tools()

    assert discovered[SERVER_ID] == (
        "get_employee_information",
        "publish_internal_note",
        "search_internal_documents",
    )


@pytest.mark.parametrize("follow_up", ["1", "search more broadly"])
async def test_follow_up_receives_history_and_reuses_tool_approval(
    local_mcp_url: str,
    follow_up: str,
) -> None:
    load_spec = RUNNER["load_spec"]
    spec = load_spec(mcp_url=local_mcp_url)
    model = ContextAwareSearchModel(follow_up)
    approvals = InMemorySessionApprovalRepository()
    conversation_repository = MemoryRepository()

    async with LangGraphEngine(
        EXAMPLE,
        model_factory=lambda *_args, **_kwargs: cast(BaseChatModel, model),
        session_approval_repository=approvals,
    ) as engine:
        await engine.build(spec)
        conversations = ConversationService(
            engine,
            conversation_repository,
            system_name="Enterprise Knowledge Assistant Local MCP Approval",
        )
        session_id = await conversations.create(
            user_id="local-demo-user",
            session_id=f"follow-up-{follow_up.replace(' ', '-')}",
        )

        first, first_prompted = await _complete_conversation_turn(
            engine,
            conversations,
            session_id,
            "Find internal documents about the session approval security policy "
            "and summarize them.",
            approve_for_session=True,
        )
        second, second_prompted = await _complete_conversation_turn(
            engine,
            conversations,
            session_id,
            follow_up,
            approve_for_session=False,
        )

    assert first_prompted is True
    assert second_prompted is False
    assert [usage.name for usage in first.used_tools] == ["search_internal_documents"]
    assert [usage.name for usage in second.used_tools] == ["search_internal_documents"]
    assert model.saw_follow_up_context is True
    assert model.saw_follow_up_tool_result is True
    assert "broader search" in second.answer
    assert [
        (message.role.value, message.content)
        for message in await conversations.history(session_id)
    ] == [
        (
            "user",
            "Find internal documents about the session approval security policy "
            "and summarize them.",
        ),
        (
            "assistant",
            "I found the session approval policy.\n"
            "1. Search more broadly using authentication and access-control terms\n"
            "2. Search another system",
        ),
        ("user", follow_up),
        (
            "assistant",
            "The broader search found authentication and access-control guidance.",
        ),
    ]


def test_local_runner_uses_same_real_model_config_as_flagship() -> None:
    load_spec = RUNNER["load_spec"]
    local_spec = load_spec()
    flagship = YAMLParser().parse(str(EXAMPLE / "agents.yaml"))

    assert isinstance(local_spec.graph.node, AgentSpec)
    assert flagship.defaults is not None
    assert local_spec.graph.node.model.provider == flagship.defaults.model.provider
    assert local_spec.graph.node.model.name == flagship.defaults.model.name
    assert local_spec.graph.node.model.name != "deterministic-local-mcp-demo"


def test_configured_model_failure_is_actionable_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = RUNNER["configured_model_factory"]

    def fail_model(*args: Any, **kwargs: Any) -> BaseChatModel:
        raise RuntimeError("provider-secret-detail")

    monkeypatch.setitem(factory.__globals__, "build_chat_model", fail_model)

    with pytest.raises(ModelConfigurationError) as error:
        factory("configured-provider", "configured-model", 0.0)

    message = str(error.value)
    assert "configured-provider" in message
    assert ".env.example" in message
    assert "provider-secret-detail" not in message


def test_missing_model_credentials_name_existing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = RUNNER["configured_model_factory"]

    class MissingCredentialModel:
        lc_secrets: ClassVar[dict[str, str]] = {
            "provider_api_key": "CONFIGURED_PROVIDER_API_KEY"
        }
        provider_api_key = ""

    monkeypatch.setitem(
        factory.__globals__,
        "build_chat_model",
        lambda *args, **kwargs: cast(BaseChatModel, MissingCredentialModel()),
    )

    with pytest.raises(
        ModelConfigurationError,
        match="CONFIGURED_PROVIDER_API_KEY",
    ):
        factory("configured-provider", "configured-model", 0.0)


def test_user_message_log_redacts_common_credentials() -> None:
    safe_user_message = RUNNER["_safe_user_message"]

    sanitized = safe_user_message(
        "Find policy api_key=sk-example123456 Bearer token-value authorization=private-value"
    )

    assert "sk-example123456" not in sanitized
    assert "token-value" not in sanitized
    assert "private-value" not in sanitized
    assert sanitized == "chars=83 content=omitted"


async def test_runner_logs_history_counts_without_message_contents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    approval_demo = RUNNER["ApprovalDemo"]
    observable_repository = RUNNER["ObservableSessionApprovalRepository"]
    engine = CompletedHistoryEngine()
    conversations = ConversationService(
        cast(Any, engine),
        MemoryRepository(),
        system_name="demo",
    )
    session_id = await conversations.create(
        user_id="local-demo-user",
        session_id="history-log-session",
    )
    demo = approval_demo(
        cast(Any, engine),
        observable_repository(),
        conversations,
        provider="test-provider",
        model_name="test-model",
        session_id=session_id,
    )

    await demo.invoke("private first message")
    capsys.readouterr()
    await demo.invoke("1")
    output = capsys.readouterr().out

    assert (
        "[SESSION HISTORY] session_id=history-log-session messages_before_run=2"
        in output
    )
    assert "[MODEL CONTEXT] session_id=history-log-session message_count=3" in output
    assert "[SESSION MESSAGE APPENDED] session_id=history-log-session role=user" in output
    assert "[SESSION MESSAGE APPENDED] session_id=history-log-session role=assistant" in output
    assert "private first message" not in output
    assert len(engine.histories[-1]) == 2


async def test_same_tool_reuses_session_approval() -> None:
    harness = ApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION])

    assert await harness.invoke(
        session_id="session-one",
        tool_name="search_internal_documents",
    )
    assert await harness.invoke(
        session_id="session-one",
        tool_name="search_internal_documents",
    )

    assert len(harness.provider.requests) == 1
    assert harness.executed == [
        "mcp:local_knowledge_mcp:search_internal_documents",
        "mcp:local_knowledge_mcp:search_internal_documents",
    ]


async def test_other_tool_on_same_mcp_requires_separate_approval() -> None:
    harness = ApprovalHarness(
        [ApprovalDecision.ALLOW_FOR_SESSION, ApprovalDecision.ALLOW_FOR_SESSION]
    )

    await harness.invoke(
        session_id="session-one",
        tool_name="search_internal_documents",
    )
    await harness.invoke(
        session_id="session-one",
        tool_name="get_employee_information",
    )

    assert len(harness.provider.requests) == 2
    assert {request.invocation.tool_identity for request in harness.provider.requests} == {
        "mcp:local_knowledge_mcp:search_internal_documents",
        "mcp:local_knowledge_mcp:get_employee_information",
    }


async def test_new_session_requires_approval_again() -> None:
    harness = ApprovalHarness([ApprovalDecision.ALLOW_FOR_SESSION, ApprovalDecision.ALLOW_ONCE])

    await harness.invoke(
        session_id="session-one",
        tool_name="search_internal_documents",
    )
    await harness.invoke(
        session_id="session-two",
        tool_name="search_internal_documents",
    )

    assert len(harness.provider.requests) == 2


async def test_denied_tool_is_not_executed() -> None:
    harness = ApprovalHarness([ApprovalDecision.DENY])

    execute = await harness.invoke(
        session_id="session-deny",
        tool_name="publish_internal_note",
    )

    assert execute is False
    assert harness.executed == []


def test_local_mcp_tools_return_deterministic_mock_data() -> None:
    search = TOOLS["search_internal_documents"]
    employee = TOOLS["get_employee_information"]
    publish = TOOLS["publish_internal_note"]

    assert search("approval") == search("approval")
    assert search("approval")[0]["document_id"] == "DOC-001"
    assert employee("E-100") == {
        "employee_id": "E-100",
        "name": "Ari Cohen",
        "team": "Knowledge Platform",
        "role": "Staff Engineer",
    }
    assert publish("Title", "Body") == publish("Title", "Body")
    assert publish("Title", "Body")["status"] == "published_demo_only"
