"""End-to-end Human-in-the-Loop behavior through the real LangGraph engine.

Uses a deterministic fake chat model (no LLM/network) that calls one named tool
then answers. Tools are generated as plugin files that append to a counter file,
so "did the provider actually run?" is observable — proving nothing executes
before approval, exactly once after approval, and never after a denial.

The approval decision is purely deterministic: with ``auto`` off every tool call
is interrupted for approval regardless of its name; with ``auto`` on every tool
call executes without asking. There is no risk classification.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall as LCToolCall

from agent_engine.approvals.approval_manager import ApprovalManager
from agent_engine.approvals.errors import ApprovalAlreadyProcessed, InvalidDecision
from agent_engine.approvals.in_memory_approval_repository import InMemoryApprovalRepository
from agent_engine.approvals.in_memory_session_approval_repository import (
    InMemorySessionApprovalRepository,
)
from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.tool_execution_manager import (
    ToolExecutionManager,
    execution_id_for,
)
from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    ModelConfig,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runtime.hooks import RunContext
from agent_engine.tool_usage.models import stable_tool_call_id

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


class FakeChatModel:
    """Calls a fixed tool once, with an id stable for the input message.

    Deterministic across graph re-entry: given the same messages it returns the
    same tool call, so resume replays to the same interrupt point.
    """

    def __init__(self, tool_names: list[str] | None = None) -> None:
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> FakeChatModel:
        return FakeChatModel([t.name for t in tools])

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        if self._tool_names and not any(isinstance(m, ToolMessage) for m in messages):
            input_text = next(
                (str(m.content) for m in messages if isinstance(m, HumanMessage)), "message"
            )
            return AIMessage(
                content="",
                tool_calls=[
                    LCToolCall(
                        name=self._tool_names[0],
                        args={"message": "go"},
                        id=f"call_{input_text}",
                    )
                ],
                usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            )
        # Echo the last tool result so tests can see what reached the model.
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                return AIMessage(
                    content=f"done: {m.content}",
                    usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                )
        return AIMessage(
            content="done",
            usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        )


class ChangingToolCallIdModel(FakeChatModel):
    """Simulates a real provider assigning a fresh tool-call id on replay."""

    def __init__(
        self,
        tool_names: list[str] | None = None,
        counter: list[int] | None = None,
    ) -> None:
        super().__init__(tool_names)
        self._counter = counter if counter is not None else [0]

    def bind_tools(self, tools: list[Any]) -> ChangingToolCallIdModel:
        return ChangingToolCallIdModel([tool.name for tool in tools], self._counter)

    def _respond(self, messages: list[Any]) -> AIMessage:
        response = super()._respond(messages)
        if response.tool_calls:
            self._counter[0] += 1
            response.tool_calls[0]["id"] = f"provider-call-{self._counter[0]}"
        return response


class ChainedApprovalModel(FakeChatModel):
    """Calls two different tools in sequence before returning an answer."""

    def bind_tools(self, tools: list[Any]) -> ChainedApprovalModel:
        return ChainedApprovalModel([tool.name for tool in tools])

    def _respond(self, messages: list[Any]) -> AIMessage:
        completed_calls = sum(isinstance(message, ToolMessage) for message in messages)
        if completed_calls < len(self._tool_names):
            tool_name = self._tool_names[completed_calls]
            return AIMessage(
                content="",
                tool_calls=[
                    LCToolCall(
                        name=tool_name,
                        args={"message": tool_name},
                        id=f"provider-call-{tool_name}",
                    )
                ],
                usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            )
        return AIMessage(
            content="done",
            usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        )


class BlockingAfterToolModel(FakeChatModel):
    """Block the post-tool model turn so streamed resume cancellation is observable."""

    def __init__(
        self,
        tool_names: list[str] | None = None,
        *,
        blocked: asyncio.Event | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> None:
        super().__init__(tool_names)
        self.blocked = blocked or asyncio.Event()
        self.cancelled = cancelled or asyncio.Event()

    def bind_tools(self, tools: list[Any]) -> BlockingAfterToolModel:
        return BlockingAfterToolModel(
            [tool.name for tool in tools],
            blocked=self.blocked,
            cancelled=self.cancelled,
        )

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        if not any(isinstance(message, ToolMessage) for message in messages):
            return self._respond(messages)
        self.blocked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield await self.ainvoke(messages)


class SequentialToolModel(FakeChatModel):
    """Request each bound tool in order, producing two approval checkpoints."""

    def bind_tools(self, tools: list[Any]) -> SequentialToolModel:
        return SequentialToolModel([tool.name for tool in tools])

    def _respond(self, messages: list[Any]) -> AIMessage:
        completed = sum(isinstance(message, ToolMessage) for message in messages)
        if completed < len(self._tool_names):
            tool_name = self._tool_names[completed]
            return AIMessage(
                content="",
                tool_calls=[
                    LCToolCall(
                        name=tool_name,
                        args={"message": tool_name},
                        id=f"call-{completed + 1}",
                    )
                ],
                usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            )
        return AIMessage(
            content="both done",
            usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        )


def _factory(provider: str, name: str, temperature: float | None, **_: Any) -> BaseChatModel:
    return cast(BaseChatModel, FakeChatModel())


def _changing_id_factory(
    provider: str, name: str, temperature: float | None, **_: Any
) -> BaseChatModel:
    return cast(BaseChatModel, ChangingToolCallIdModel())


def _chained_factory(
    provider: str, name: str, temperature: float | None, **_: Any
) -> BaseChatModel:
    return cast(BaseChatModel, ChainedApprovalModel())


def _sequential_factory(
    provider: str, name: str, temperature: float | None, **_: Any
) -> BaseChatModel:
    return cast(BaseChatModel, SequentialToolModel())


def _write_counting_tool(base_dir: Path, tool_id: str, counter: Path) -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(
        f"def {tool_id}(message: str) -> str:\n"
        f"    with open({str(counter)!r}, 'a') as f:\n"
        "        f.write('x')\n"
        "    return 'sent: ' + message\n",
        encoding="utf-8",
    )


def _write_structured_counting_tool(base_dir: Path, tool_id: str, counter: Path) -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(
        f"def {tool_id}(message: str) -> dict:\n"
        f"    with open({str(counter)!r}, 'a') as f:\n"
        "        f.write('x')\n"
        "    return {'status': 'sent', 'message': message}\n",
        encoding="utf-8",
    )


def _spec(tool_id: str, *, auto_mode: bool = False) -> SystemSpec:
    agent = AgentSpec(
        id="writer",
        name="writer",
        description="writer agent",
        model=_MODEL,
        prompts=BasePromptSet(),
        tools=(ToolSpec(tool_id, "Send an email to the selected recipient."),),
        auto_mode=auto_mode,
    )
    return SystemSpec(meta=SystemMeta(name="hitl"), defaults=None, graph=GraphNode(node=agent))


def _chained_spec() -> SystemSpec:
    return _multi_tool_spec("send_email", "archive_email")


def _multi_tool_spec(*tool_ids: str) -> SystemSpec:
    agent = AgentSpec(
        id="writer",
        name="writer",
        description="writer agent",
        model=_MODEL,
        prompts=BasePromptSet(),
        tools=tuple(ToolSpec(tool_id, f"{tool_id} description") for tool_id in tool_ids),
        auto_mode=False,
    )
    return SystemSpec(meta=SystemMeta(name="hitl"), defaults=None, graph=GraphNode(node=agent))


def _executions(counter: Path) -> int:
    return len(counter.read_text()) if counter.exists() else 0


async def _engine(tmp_path: Path) -> LangGraphEngine:
    engine = LangGraphEngine(tmp_path, model_factory=_factory)
    return engine


# --------------------------------------------------------------------------- #


async def test_tool_requires_approval_and_does_not_execute(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        result = await engine.run("hi", context=RunContext(run_id="run-1"))

    assert result.status == "pending_approval"
    assert result.pending_approval is not None
    assert result.pending_approval.tool_name == "send_email"
    assert result.pending_approval.agent_id == "writer"
    assert result.pending_approval.description == (
        "Send an email to the selected recipient. This action has not been executed."
    )
    assert "writer" not in result.pending_approval.description
    # The provider must NOT have been invoked before approval.
    assert _executions(counter) == 0


async def test_allow_once_resumes_same_run_and_executes_once(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="run-1"))
        assert pending.pending_approval is not None
        assert (pending.input_tokens, pending.output_tokens) == (2, 1)
        approval_id = pending.pending_approval.approval_id

        resumed = await engine.resume("run-1", approval_id, "allow once")
        recovered = await engine.get_processed_result("run-1", approval_id)

    assert resumed.status == "completed"
    assert "sent: go" in resumed.answer
    assert _executions(counter) == 1  # executed exactly once
    assert resumed.visited == ["writer"]  # same run, agent not re-selected as a new route
    assert (resumed.input_tokens, resumed.output_tokens) == (6, 3)
    assert recovered == resumed


async def test_hitl_resume_persists_structured_local_result(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    tool_name = "send_structured"
    _write_structured_counting_tool(tmp_path, tool_name, counter)
    manager = ToolExecutionManager(execution_repository=InMemoryToolExecutionRepository())
    async with LangGraphEngine(
        tmp_path,
        model_factory=_factory,
        execution_manager=manager,
    ) as engine:
        await engine.build(_spec(tool_name))
        pending = await engine.run("hi", context=RunContext(run_id="run-structured"))
        assert pending.pending_approval is not None

        resumed = await engine.resume(
            "run-structured",
            pending.pending_approval.approval_id,
            "allow once",
        )

    tool_call_id = stable_tool_call_id(
        "run-structured",
        "writer",
        "local",
        None,
        tool_name,
        {"message": "go"},
    )
    persisted = await manager.restored_result(execution_id_for(tool_call_id))
    assert resumed.status == "completed"
    assert _executions(counter) == 1
    assert persisted is not None
    assert persisted.structured == {"message": "go", "status": "sent"}


async def test_retrying_first_decision_recovers_second_pending_approval(tmp_path: Path) -> None:
    first_counter = tmp_path / "first.log"
    second_counter = tmp_path / "second.log"
    _write_counting_tool(tmp_path, "send_email", first_counter)
    _write_counting_tool(tmp_path, "archive_email", second_counter)

    async with LangGraphEngine(tmp_path, model_factory=_chained_factory) as engine:
        await engine.build(_chained_spec())
        first = await engine.run("hi", context=RunContext(run_id="run-chained"))
        assert first.pending_approval is not None

        second = await engine.resume(
            "run-chained",
            first.pending_approval.approval_id,
            "allow once",
        )
        assert second.pending_approval is not None
        recovered = await engine.get_processed_result(
            "run-chained",
            first.pending_approval.approval_id,
        )

    assert second.status == "pending_approval"
    assert second.pending_approval.tool_name == "archive_email"
    assert recovered == second
    assert _executions(first_counter) == 1
    assert _executions(second_counter) == 0


async def test_resume_is_stable_when_provider_changes_tool_call_id(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with LangGraphEngine(tmp_path, model_factory=_changing_id_factory) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="run-changing-id"))
        assert pending.pending_approval is not None

        resumed = await engine.resume(
            "run-changing-id",
            pending.pending_approval.approval_id,
            "allow once",
        )

    assert resumed.status == "completed"
    assert resumed.pending_approval is None
    assert _executions(counter) == 1


async def test_deny_resumes_without_executing(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="run-2"))
        assert pending.pending_approval is not None
        resumed = await engine.resume("run-2", pending.pending_approval.approval_id, "deny")

    assert resumed.status == "completed"
    assert _executions(counter) == 0
    assert "denied" in resumed.answer.lower()


async def test_allow_for_session_suppresses_later_prompt_same_conversation(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        # First run in conversation "conv-1": approval is requested.
        pending = await engine.run(
            "hi",
            context=RunContext(run_id="run-a", conversation_id="conv-1", user_id="user-1"),
        )
        assert pending.pending_approval is not None
        resumed = await engine.resume(
            "run-a",
            pending.pending_approval.approval_id,
            "allow for this session",
            caller_user_id="user-1",
            caller_session_id="conv-1",
        )
        assert resumed.status == "completed"

        # Second run in the SAME conversation: no approval prompt this time.
        second = await engine.run(
            "again",
            context=RunContext(run_id="run-b", conversation_id="conv-1", user_id="user-1"),
        )
        assert second.status == "completed"
        assert second.pending_approval is None
        assert _executions(counter) == 2

        # The same conversation id must not leak permission to another user.
        other_user = await engine.run(
            "other-user",
            context=RunContext(run_id="run-c", conversation_id="conv-1", user_id="user-2"),
        )
        assert other_user.status == "pending_approval"

        # A different conversation still requires approval (session is scoped).
        other = await engine.run(
            "other-session",
            context=RunContext(run_id="run-d", conversation_id="conv-2", user_id="user-1"),
        )
        assert other.status == "pending_approval"


async def test_session_permission_survives_engine_reconstruction(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    repository = InMemorySessionApprovalRepository()
    context = RunContext(run_id="run-first", conversation_id="conv-1", user_id="user-1")

    async with LangGraphEngine(
        tmp_path,
        model_factory=_factory,
        session_approval_repository=repository,
    ) as first_engine:
        await first_engine.build(_spec("send_email"))
        pending = await first_engine.run("first", context=context)
        assert pending.pending_approval is not None
        await first_engine.resume(
            "run-first",
            pending.pending_approval.approval_id,
            "allow for this session",
            caller_user_id="user-1",
            caller_session_id="conv-1",
        )

    async with LangGraphEngine(
        tmp_path,
        model_factory=_factory,
        session_approval_repository=repository,
    ) as second_engine:
        await second_engine.build(_spec("send_email"))
        later = await second_engine.run(
            "later",
            context=RunContext(
                run_id="run-later",
                conversation_id="conv-1",
                user_id="user-1",
            ),
        )

    assert later.status == "completed"
    assert later.pending_approval is None
    assert _executions(counter) == 2


async def test_auto_mode_executes_without_interrupt(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email", auto_mode=True))
        result = await engine.run("hi", context=RunContext(run_id="run-3"))

    assert result.status == "completed"
    assert result.pending_approval is None
    assert _executions(counter) == 1


async def test_auto_mode_executes_any_tool_no_classification(tmp_path: Path) -> None:
    # A name that a risk classifier would have blocked runs freely under auto:
    # the new design performs no classification.
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "drop_database", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("drop_database", auto_mode=True))
        result = await engine.run("hi", context=RunContext(run_id="run-4"))

    assert result.status == "completed"
    assert _executions(counter) == 1


async def test_duplicate_resume_is_rejected_and_tool_runs_once(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="run-5"))
        assert pending.pending_approval is not None
        approval_id = pending.pending_approval.approval_id

        await engine.resume("run-5", approval_id, "allow once")
        with pytest.raises(ApprovalAlreadyProcessed):
            await engine.resume("run-5", approval_id, "allow once")

    assert _executions(counter) == 1  # no duplicate side effect


async def test_cancel_pending_approval_is_terminal_and_never_executes_tool(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run(
            "hi",
            context=RunContext(
                run_id="run-cancel-pending",
                conversation_id="conversation-1",
                user_id="owner",
            ),
        )
        assert pending.pending_approval is not None

        await engine.cancel_pending_approval(
            "run-cancel-pending",
            pending.pending_approval.approval_id,
            caller_user_id="owner",
            caller_session_id="conversation-1",
        )

        assert await engine.get_run_status("run-cancel-pending") == "cancelled"
        assert await engine.get_pending_approval("run-cancel-pending") is None
        with pytest.raises(ApprovalAlreadyProcessed):
            await engine.resume(
                "run-cancel-pending",
                pending.pending_approval.approval_id,
                "allow once",
                caller_user_id="owner",
                caller_session_id="conversation-1",
            )

    assert _executions(counter) == 0


async def test_stopping_streamed_resume_cancels_same_run_and_real_graph_task(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    model = BlockingAfterToolModel()

    def factory(*_: Any, **__: Any) -> BaseChatModel:
        return cast(BaseChatModel, model)

    async with LangGraphEngine(tmp_path, model_factory=factory) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run(
            "hi",
            context=RunContext(
                run_id="same-run",
                conversation_id="conversation-1",
                user_id="owner",
            ),
        )
        assert pending.pending_approval is not None

        events = cast(
            AsyncGenerator[Any, None],
            engine.resume_stream(
                "same-run",
                pending.pending_approval.approval_id,
                "allow once",
                caller_user_id="owner",
                caller_session_id="conversation-1",
            ),
        )
        started = await events.__anext__()
        assert started.type == "resume_started"
        assert started.run_id == "same-run"
        assert await engine.get_run_status("same-run") == "running"
        await asyncio.wait_for(model.blocked.wait(), timeout=1)

        await events.aclose()
        await asyncio.wait_for(model.cancelled.wait(), timeout=1)

        assert await engine.get_run_status("same-run") == "cancelled"

    assert _executions(counter) == 1


async def test_stopping_resume_during_claim_activation_cannot_strand_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="activation-race"))
        assert pending.pending_approval is not None
        assert engine._lifecycle is not None

        activation_entered = asyncio.Event()
        release_activation = asyncio.Event()
        original_activate = engine._lifecycle.activate_resume

        async def delayed_activate(ctx: RunContext) -> None:
            activation_entered.set()
            await release_activation.wait()
            await original_activate(ctx)

        monkeypatch.setattr(engine._lifecycle, "activate_resume", delayed_activate)
        events = cast(
            AsyncGenerator[Any, None],
            engine.resume_stream(
                "activation-race",
                pending.pending_approval.approval_id,
                "allow once",
            ),
        )
        first_event = asyncio.create_task(events.__anext__())
        await asyncio.wait_for(activation_entered.wait(), timeout=1)

        first_event.cancel()
        await asyncio.sleep(0)
        release_activation.set()
        with pytest.raises(asyncio.CancelledError):
            await first_event
        await events.aclose()

        assert await engine.get_run_status("activation-race") == "cancelled"
        assert await engine.get_pending_approval("activation-race") is None

    assert _executions(counter) == 0


async def test_completed_streamed_resume_wins_over_late_stop(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="completed-resume"))
        assert pending.pending_approval is not None
        events = cast(
            AsyncGenerator[Any, None],
            engine.resume_stream(
                "completed-resume",
                pending.pending_approval.approval_id,
                "allow once",
            ),
        )

        streamed = [event async for event in events]
        await events.aclose()

        assert streamed[0].type == "resume_started"
        assert streamed[-1].type == "final"
        assert await engine.get_run_status("completed-resume") == "completed"

    assert _executions(counter) == 1


async def test_streamed_resume_supports_multiple_sequential_approvals_same_run(
    tmp_path: Path,
) -> None:
    first_counter = tmp_path / "first.log"
    second_counter = tmp_path / "second.log"
    _write_counting_tool(tmp_path, "first_tool", first_counter)
    _write_counting_tool(tmp_path, "second_tool", second_counter)
    async with LangGraphEngine(tmp_path, model_factory=_sequential_factory) as engine:
        await engine.build(_multi_tool_spec("first_tool", "second_tool"))
        first = await engine.run("hi", context=RunContext(run_id="multi-approval"))
        assert first.pending_approval is not None
        assert first.pending_approval.tool_name == "first_tool"

        first_resume = [
            event
            async for event in engine.resume_stream(
                "multi-approval",
                first.pending_approval.approval_id,
                "allow once",
            )
        ]
        second = next(event for event in first_resume if event.type == "pending_approval")
        assert second.run_id == "multi-approval"
        assert second.tool_name == "second_tool"
        assert await engine.get_run_status("multi-approval") == "pending_approval"

        second_resume = [
            event
            async for event in engine.resume_stream(
                "multi-approval",
                second.approval_id or "",
                "allow once",
            )
        ]
        assert second_resume[0].type == "resume_started"
        assert second_resume[-1].type == "final"
        assert second_resume[-1].content == "both done"
        assert await engine.get_run_status("multi-approval") == "completed"

    assert _executions(first_counter) == 1
    assert _executions(second_counter) == 1


async def test_invalid_decision_fails_closed_without_tool_execution(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="run-invalid"))
        assert pending.pending_approval is not None

        with pytest.raises(InvalidDecision):
            await engine.resume(
                "run-invalid",
                pending.pending_approval.approval_id,
                "not-a-decision",
            )

    assert _executions(counter) == 0


async def test_missing_session_identity_never_persists_session_permission(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        first = await engine.run("first", context=RunContext(run_id="run-no-session-1"))
        assert first.pending_approval is not None
        await engine.resume(
            "run-no-session-1",
            first.pending_approval.approval_id,
            "allow for this session",
        )

        second = await engine.run("second", context=RunContext(run_id="run-no-session-2"))

    assert second.status == "pending_approval"
    assert _executions(counter) == 1


class FailingApprovalManager(ApprovalManager):
    async def create_pending(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("approval provider unavailable")


async def test_approval_provider_failure_fails_closed(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    manager = FailingApprovalManager(
        run_repository=InMemoryRunRepository(),
        approval_repository=InMemoryApprovalRepository(),
    )
    async with LangGraphEngine(
        tmp_path,
        model_factory=_factory,
        approval_manager=manager,
    ) as engine:
        await engine.build(_spec("send_email"))
        with pytest.raises(RuntimeError, match="approval provider unavailable"):
            await engine.run("hi", context=RunContext(run_id="run-provider-failure"))

    assert _executions(counter) == 0


async def test_pending_approval_query_and_run_status(tmp_path: Path) -> None:
    _write_counting_tool(tmp_path, "send_email", tmp_path / "calls.log")
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        await engine.run("hi", context=RunContext(run_id="run-6"))

        assert await engine.get_run_status("run-6") == "pending_approval"
        pa = await engine.get_pending_approval("run-6")
        assert pa is not None and pa.tool_name == "send_email"

        await engine.resume("run-6", pa.approval_id, "allow once")
        assert await engine.get_run_status("run-6") == "completed"
        assert await engine.get_pending_approval("run-6") is None
