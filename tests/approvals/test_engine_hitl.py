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

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall as LCToolCall

from agent_engine.approvals.errors import ApprovalAlreadyProcessed, InvalidDecision
from agent_engine.approvals.manager import ApprovalManager
from agent_engine.approvals.repository import (
    InMemoryApprovalRepository,
    InMemoryRunRepository,
)
from agent_engine.approvals.session_store import InMemorySessionApprovalRepository
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
from agent_engine.runtime.hooks import RunContext

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
            )
        # Echo the last tool result so tests can see what reached the model.
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                return AIMessage(content=f"done: {m.content}")
        return AIMessage(content="done")


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


def _factory(provider: str, name: str, temperature: float | None, **_: Any) -> BaseChatModel:
    return cast(BaseChatModel, FakeChatModel())


def _changing_id_factory(
    provider: str, name: str, temperature: float | None, **_: Any
) -> BaseChatModel:
    return cast(BaseChatModel, ChangingToolCallIdModel())


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


def _spec(tool_id: str, *, auto_mode: bool = False) -> SystemSpec:
    agent = AgentSpec(
        id="writer",
        name="writer",
        description="writer agent",
        model=_MODEL,
        prompts=BasePromptSet(),
        tools=(ToolSpec(tool_id, f"{tool_id} description"),),
        auto_mode=auto_mode,
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
    assert result.pending_approval.description  # human-readable, "not executed yet"
    # The provider must NOT have been invoked before approval.
    assert _executions(counter) == 0


async def test_allow_once_resumes_same_run_and_executes_once(tmp_path: Path) -> None:
    counter = tmp_path / "calls.log"
    _write_counting_tool(tmp_path, "send_email", counter)
    async with await _engine(tmp_path) as engine:
        await engine.build(_spec("send_email"))
        pending = await engine.run("hi", context=RunContext(run_id="run-1"))
        assert pending.pending_approval is not None
        approval_id = pending.pending_approval.approval_id

        resumed = await engine.resume("run-1", approval_id, "allow once")

    assert resumed.status == "completed"
    assert "sent: go" in resumed.answer
    assert _executions(counter) == 1  # executed exactly once
    assert resumed.visited == ["writer"]  # same run, agent not re-selected as a new route


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
