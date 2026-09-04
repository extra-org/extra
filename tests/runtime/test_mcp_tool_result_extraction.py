"""An MCP tool's result reaches the model as clean text, not a repr string.

MCP tools are LangChain ``StructuredTool``s built with
``response_format="content_and_artifact"``: their real return value is a list
of content blocks (``[{"type": "text", "text": "..."}]``), not a plain string.
Calling them the wrong way (a bare args dict, no ``tool_call_id``) makes
LangChain hand back that raw list unwrapped, and blindly ``str()``-ing it
produces Python-repr punctuation instead of the text itself. These tests drive
a real ``content_and_artifact`` tool through the engine (the same way
``tests/runtime/test_tool_hooks.py`` injects a fake MCP tool) and inspect the
exact text the model received.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.tools import StructuredTool

from agent_engine.approvals.approval_provider import ApprovalProvider, ApprovalRequest
from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.models import ToolExecutionStatus
from agent_engine.approvals.tool_execution_manager import ToolExecutionManager
from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    HooksConfig,
    MCPSpec,
    ModelConfig,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.langgraph.tools.agent_tool_binding import AgentToolBinding
from agent_engine.engine.langgraph.tools.tool_invoker import ToolInvoker
from agent_engine.runtime.hooks import HookManager, RunContext, current_run_context
from agent_engine.runtime.tool_results import NormalizedToolResult, PersistedToolResult
from agent_engine.tool_usage.in_memory import InMemoryToolUsageRepository
from agent_engine.tool_usage.tracker import ToolUsageTracker

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


class _UnusedApprovalProvider(ApprovalProvider):
    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        raise AssertionError("auto-mode tool execution must not request approval")


class _CapturingExecutionRepository(InMemoryToolExecutionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.completed_result: PersistedToolResult | None = None

    async def complete(
        self,
        execution_id: str,
        status: ToolExecutionStatus,
        result: PersistedToolResult,
    ) -> None:
        await super().complete(execution_id, status, result)
        self.completed_result = result


class _BlockingCompletionRepository(InMemoryToolExecutionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.completing = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(
        self,
        execution_id: str,
        status: ToolExecutionStatus,
        result: PersistedToolResult,
    ) -> None:
        self.completing.set()
        await self.release.wait()
        await super().complete(execution_id, status, result)


class EchoToolResultModel:
    """Calls the tool once, then answers with exactly the tool result text it
    was given — lets a test see precisely what reached the conversation.
    """

    def __init__(
        self,
        tool_names: list[str] | None = None,
        tool_args: dict[str, Any] | None = None,
        captured: list[ToolMessage] | None = None,
    ) -> None:
        self._tool_names = tool_names or []
        self._tool_args = tool_args or {}
        self._captured = captured if captured is not None else []

    def bind_tools(self, tools: list[Any]) -> EchoToolResultModel:
        return EchoToolResultModel([t.name for t in tools], self._tool_args, self._captured)

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if tool_msgs:
            self._captured.append(tool_msgs[-1])
        if self._tool_names and not tool_msgs:
            return AIMessage(
                content="",
                tool_calls=[ToolCall(name=self._tool_names[0], args=self._tool_args, id="c1")],
            )
        return AIMessage(content=tool_msgs[-1].content if tool_msgs else "no-tool")


def _model_factory(
    tool_args: dict[str, Any] | None = None,
    captured: list[ToolMessage] | None = None,
) -> Any:
    def factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        return cast(
            BaseChatModel,
            EchoToolResultModel(tool_args=tool_args, captured=captured),
        )

    return factory


def _agent(node_id: str, **kw: Any) -> GraphNode:
    kw.setdefault("auto_mode", True)
    return GraphNode(
        node=AgentSpec(
            id=node_id,
            name=node_id,
            description=f"{node_id} agent",
            model=_MODEL,
            prompts=BasePromptSet(),
            **kw,
        )
    )


def _system(graph: GraphNode) -> SystemSpec:
    return SystemSpec(
        meta=SystemMeta(name="mcp-result-extraction"),
        defaults=None,
        graph=graph,
        hooks=HooksConfig(hooks=()),
    )


async def _run_with_mcp_tool(
    tmp_path: Path, mcp_tool: StructuredTool
) -> tuple[str, NormalizedToolResult, ToolMessage]:
    spec = _system(_agent("research", mcps=(MCPSpec(id="wiki", url="https://wiki.test/mcp"),)))
    captured: list[ToolMessage] = []
    repository = _CapturingExecutionRepository()
    manager = ToolExecutionManager(execution_repository=repository)
    async with LangGraphEngine(
        tmp_path,
        model_factory=_model_factory(captured=captured),
        execution_manager=manager,
    ) as engine:
        await engine.build(spec)
        engine._mcp_tools["wiki"] = [mcp_tool]
        engine._app = engine._build_graph(spec)
        result = await engine.run("search please")
    assert repository.completed_result is not None
    normalized = NormalizedToolResult.from_persisted(repository.completed_result)
    return result.answer, normalized, captured[-1]


async def test_mcp_text_result_is_not_garbled(tmp_path: Path) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [{"type": "text", "text": "clean text", "id": "lc_1"}], {
            "structuredContent": {"value": "clean text"}
        }

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="wiki_search",
        description="search",
        response_format="content_and_artifact",
    )

    answer, runtime_result, message = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == "clean text"
    assert runtime_result.text == "clean text"
    assert runtime_result.structured == {"value": "clean text"}
    assert message.artifact is None
    assert "'type':" not in answer
    assert "'text':" not in answer


async def test_mcp_multi_block_text_result_is_joined(tmp_path: Path) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [
            {"type": "text", "text": "first block", "id": "lc_1"},
            {"type": "text", "text": "second block", "id": "lc_2"},
        ], {"structured_content": {"count": 2}}

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="wiki_search",
        description="search",
        response_format="content_and_artifact",
    )

    answer, runtime_result, _ = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert "first block" in answer
    assert "second block" in answer
    assert "'type':" not in answer
    assert runtime_result.structured == {"count": 2}


async def test_mcp_structured_only_result_is_preserved(tmp_path: Path) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [], {"structured_content": {"balance": 1250}}

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="account_balance",
        description="balance",
        response_format="content_and_artifact",
    )

    answer, runtime_result, message = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == '{"balance":1250}'
    assert message.content == '{"balance":1250}'
    assert message.artifact is None
    assert runtime_result.structured == {"balance": 1250}


async def test_structured_only_fallback_is_deterministic_json(tmp_path: Path) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [], {"structured_content": {"z": 1, "a": [2, 1]}}

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="deterministic_result",
        description="result",
        response_format="content_and_artifact",
    )

    answer, runtime_result, _ = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == '{"a":[2,1],"z":1}'
    assert runtime_result.text == answer
    assert runtime_result.structured == {"a": [2, 1], "z": 1}


async def test_unsupported_mcp_block_does_not_discard_structured_result(
    tmp_path: Path,
) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [{"type": "image", "url": "https://files.test/chart.png"}], {
            "structured_content": {"chart_id": "chart-1"}
        }

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="chart",
        description="chart",
        response_format="content_and_artifact",
    )

    answer, runtime_result, _ = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == "[unsupported image block]"
    assert runtime_result.structured == {"chart_id": "chart-1"}


async def test_artifact_metadata_is_preserved_without_binary_body(tmp_path: Path) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [{"type": "text", "text": "report ready"}], {
            "structured_content": {"report_id": "r-1"},
            "file": {"uri": "s3://reports/r-1.pdf", "mime_type": "application/pdf"},
            "preview": b"binary-preview",
        }

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="report",
        description="report",
        response_format="content_and_artifact",
    )

    answer, runtime_result, message = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == "report ready"
    assert message.artifact is None
    assert runtime_result.structured == {"report_id": "r-1"}
    assert runtime_result.artifact == {
        "file": {"uri": "s3://reports/r-1.pdf", "mime_type": "application/pdf"},
        "preview": {"type": "binary", "size": 14, "omitted": True},
    }


async def test_structured_result_values_are_not_automatically_logged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_value = "structured-value-must-stay-private"

    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [{"type": "text", "text": "complete"}], {
            "structured_content": {"private": sensitive_value}
        }

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="private_report",
        description="report",
        response_format="content_and_artifact",
    )

    with caplog.at_level(logging.DEBUG):
        _, runtime_result, message = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert runtime_result.structured == {"private": sensitive_value}
    assert message.artifact is None
    assert sensitive_value not in caplog.text
    assert all(
        sensitive_value not in repr(getattr(record, "fields", {})) for record in caplog.records
    )


async def test_malformed_artifact_fails_with_controlled_model_text(tmp_path: Path) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], str]:
        return [{"type": "text", "text": "valid text"}], "not-a-mapping"

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="malformed",
        description="malformed",
        response_format="content_and_artifact",
    )

    answer, runtime_result, _ = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == "Tool error: invalid tool result"
    assert runtime_result == NormalizedToolResult.text_only(answer)


async def test_non_json_structured_value_fails_without_using_repr_or_logging_data(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    unexpected = object()
    sensitive_key = "private-account-identifier"

    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [{"type": "text", "text": "valid text"}], {
            "structured_content": {sensitive_key: unexpected}
        }

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="non_json",
        description="non-json",
        response_format="content_and_artifact",
    )

    with caplog.at_level(logging.DEBUG):
        answer, runtime_result, _ = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == "Tool error: invalid tool result"
    assert hex(id(unexpected)) not in answer
    assert sensitive_key not in answer
    assert hex(id(unexpected)) not in caplog.text
    assert sensitive_key not in caplog.text
    assert runtime_result == NormalizedToolResult.text_only(answer)


async def test_oversized_mcp_structured_result_fails_with_controlled_text(
    tmp_path: Path,
) -> None:
    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        return [{"type": "text", "text": "valid text"}], {"structured_content": list(range(10_001))}

    mcp_tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="oversized",
        description="oversized",
        response_format="content_and_artifact",
    )

    answer, runtime_result, _ = await _run_with_mcp_tool(tmp_path, mcp_tool)

    assert answer == "Tool error: invalid tool result"
    assert runtime_result == NormalizedToolResult.text_only(answer)


def _write_tool(base_dir: Path, tool_id: str) -> None:
    body = f"def {tool_id}(message: str) -> str:\n    return 'did: ' + message\n"
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(body, encoding="utf-8")


async def test_local_tool_result_unaffected(tmp_path: Path) -> None:
    # Local tools default to response_format="content" (a plain string, not
    # MCP content blocks) — regression guard that this fix leaves that path
    # exactly as before.
    _write_tool(tmp_path, "book_flight")
    spec = _system(_agent("flights", tools=(ToolSpec("book_flight", "book"),)))

    factory = _model_factory(tool_args={"message": "go"})
    async with LangGraphEngine(tmp_path, model_factory=factory) as engine:
        await engine.build(spec)
        result = await engine.run("book please")

    assert result.answer == "did: go"


async def test_local_structured_tool_preserves_data_and_existing_text(tmp_path: Path) -> None:
    tool_id = "get_orders"
    body = f"def {tool_id}(message: str) -> dict:\n    return {{'orders': [{{'id': 'ORDER-1'}}]}}\n"
    tools_dir = tmp_path / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(body, encoding="utf-8")
    spec = _system(_agent("orders", tools=(ToolSpec(tool_id, "orders"),)))
    captured: list[ToolMessage] = []
    repository = _CapturingExecutionRepository()

    factory = _model_factory(tool_args={"message": "go"}, captured=captured)
    async with LangGraphEngine(
        tmp_path,
        model_factory=factory,
        execution_manager=ToolExecutionManager(execution_repository=repository),
    ) as engine:
        await engine.build(spec)
        result = await engine.run("orders please")

    assert repository.completed_result is not None
    runtime_result = NormalizedToolResult.from_persisted(repository.completed_result)
    assert result.answer == '{"orders": [{"id": "ORDER-1"}]}'
    assert captured[-1].artifact is None
    assert runtime_result.structured == {"orders": [{"id": "ORDER-1"}]}


async def test_idempotent_replay_restores_identical_normalized_result() -> None:
    calls = 0

    def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [{"type": "text", "text": "Found 2 invoices"}], {
            "structured_content": {"count": 2},
            "source": "billing",
        }

    tool = StructuredTool.from_function(
        fake_mcp_tool,
        name="invoice_search",
        description="search invoices",
        response_format="content_and_artifact",
    )
    execution_manager = ToolExecutionManager(execution_repository=InMemoryToolExecutionRepository())
    invoker = _invoker(tool, execution_manager)
    tool_call = {"id": "call-1", "name": tool.name, "args": {}}
    token = current_run_context.set(RunContext(run_id="run-1"))
    try:
        first = await invoker.invoke(tool_call)
        replayed = await invoker.invoke(tool_call)
    finally:
        current_run_context.reset(token)

    assert calls == 1
    assert replayed == first
    assert replayed.text == "Found 2 invoices"
    assert replayed.structured == {"count": 2}
    assert replayed.artifact == {"source": "billing"}


async def test_concurrent_duplicate_calls_share_one_provider_execution() -> None:
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_mcp_tool() -> tuple[list[dict[str, str]], dict[str, Any]]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [{"type": "text", "text": "done"}], {"structured_content": {"ok": True}}

    tool = StructuredTool.from_function(
        coroutine=fake_mcp_tool,
        name="slow_tool",
        description="slow",
        response_format="content_and_artifact",
    )
    invoker = _invoker(
        tool,
        ToolExecutionManager(execution_repository=InMemoryToolExecutionRepository()),
    )
    tool_call = {"id": "call-1", "name": tool.name, "args": {}}
    token = current_run_context.set(RunContext(run_id="run-concurrent"))
    try:
        owner = asyncio.create_task(invoker.invoke(tool_call))
        await started.wait()
        duplicate = asyncio.create_task(invoker.invoke(tool_call))
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(owner, duplicate)
    finally:
        current_run_context.reset(token)

    assert calls == 1
    assert first == second == NormalizedToolResult("done", structured={"ok": True})


async def test_cancellation_during_failed_result_write_does_not_strand_duplicate() -> None:
    calls = 0

    async def failing_tool() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider failed")

    tool = StructuredTool.from_function(
        coroutine=failing_tool,
        name="failing_tool",
        description="fails",
    )
    repository = _BlockingCompletionRepository()
    invoker = _invoker(
        tool,
        ToolExecutionManager(execution_repository=repository),
    )
    tool_call = {"id": "call-1", "name": tool.name, "args": {}}
    token = current_run_context.set(RunContext(run_id="run-cancelled-failure"))
    owner = asyncio.create_task(invoker.invoke(tool_call))
    try:
        await repository.completing.wait()
        duplicate = asyncio.create_task(invoker.invoke(tool_call))
        owner.cancel()
        await asyncio.sleep(0)
        assert duplicate.done() is False

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        replayed = await asyncio.wait_for(duplicate, timeout=1)
    finally:
        repository.release.set()
        if not owner.done():
            owner.cancel()
        current_run_context.reset(token)

    assert calls == 1
    assert replayed == NormalizedToolResult.text_only("Tool error: provider failed")


async def test_cancellation_during_successful_result_write_preserves_success() -> None:
    calls = 0

    async def successful_tool() -> str:
        nonlocal calls
        calls += 1
        return "completed"

    tool = StructuredTool.from_function(
        coroutine=successful_tool,
        name="successful_tool",
        description="succeeds",
    )
    repository = _BlockingCompletionRepository()
    invoker = _invoker(
        tool,
        ToolExecutionManager(execution_repository=repository),
    )
    tool_call = {"id": "call-1", "name": tool.name, "args": {}}
    token = current_run_context.set(RunContext(run_id="run-cancelled-success"))
    owner = asyncio.create_task(invoker.invoke(tool_call))
    try:
        await repository.completing.wait()
        duplicate = asyncio.create_task(invoker.invoke(tool_call))
        owner.cancel()
        await asyncio.sleep(0)
        assert duplicate.done() is False

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        replayed = await asyncio.wait_for(duplicate, timeout=1)
    finally:
        repository.release.set()
        if not owner.done():
            owner.cancel()
        current_run_context.reset(token)

    assert calls == 1
    assert replayed == NormalizedToolResult.text_only("completed")


def _invoker(tool: StructuredTool, execution_manager: ToolExecutionManager) -> ToolInvoker:
    return ToolInvoker(
        spec=AgentSpec(
            id="billing",
            name="billing",
            description="billing",
            model=_MODEL,
            auto_mode=True,
        ),
        node_path="billing",
        binding=AgentToolBinding(
            tools={tool.name: tool},
            mcp_tool_names=frozenset({tool.name}),
            mcp_server_by_tool={tool.name: "billing-mcp"},
        ),
        hook_manager=HookManager.empty(),
        execution_manager=execution_manager,
        approval_coordinator=ApprovalCoordinator(_UnusedApprovalProvider()),
        usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
    )
