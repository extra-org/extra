"""Lifecycle of a streamed run, end to end through the real LangGraph engine.

``stream`` splits one run across two tasks — a producer executing the graph and
the consumer iterating the generator — so the things that can silently break are
the seams between them: where errors surface, which run status a terminal event
leaves behind, whether hooks fire on the right paths, and whether the per-run
context vars are cleaned up when the caller walks away mid-stream.

Models are faked through the engine's ``model_factory`` seam; no LLM, no network.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import ToolCall

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    HooksConfig,
    HookSpec,
    ModelConfig,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.runtime.execution import current_execution
from agent_engine.runtime.hooks import RunContext, current_run_context
from agent_engine.runtime.streaming import RunStreamEvent, StreamSinks, current_streams
from tests.runtime.hooks import fixtures

_FIX = "tests.runtime.hooks.fixtures"
_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


class AnsweringModel:
    """Answers once, optionally reporting provider token usage."""

    def __init__(self, *, usage: dict[str, int] | None = None, tool_names: list[str] | None = None):
        self._usage = usage
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> AnsweringModel:
        return AnsweringModel(usage=self._usage, tool_names=[t.name for t in tools])

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        if self._tool_names and not any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(
                content="",
                tool_calls=[ToolCall(name=self._tool_names[0], args={"message": "x"}, id="c1")],
            )
        return AIMessage(content="ok", usage_metadata=cast(Any, self._usage))


class BrokenModel:
    def bind_tools(self, tools: list[Any]) -> BrokenModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        raise RuntimeError("model down")

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        raise RuntimeError("model down")
        yield  # pragma: no cover


class BlockingModel:
    """Emit one chunk, then block so the stream can be abandoned mid-run."""

    def __init__(self) -> None:
        self.released = asyncio.Event()
        self.resumed = False

    def bind_tools(self, tools: list[Any]) -> BlockingModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        await self.released.wait()
        return AIMessage(content="first second")

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="first")
        await self.released.wait()
        self.resumed = True
        yield AIMessageChunk(content=" second")


def _factory(*_: Any, **__: Any) -> BaseChatModel:
    return cast(BaseChatModel, cast(object, AnsweringModel()))


def _usage_factory(*_: Any, **__: Any) -> BaseChatModel:
    usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    return cast(BaseChatModel, cast(object, AnsweringModel(usage=usage)))


def _broken_factory(*_: Any, **__: Any) -> BaseChatModel:
    return cast(BaseChatModel, cast(object, BrokenModel()))


def _spec(*hooks: HookSpec, tool_id: str | None = None, auto_mode: bool = True) -> SystemSpec:
    agent = AgentSpec(
        id="solo",
        name="solo",
        description="solo agent",
        model=_MODEL,
        prompts=BasePromptSet(),
        tools=(ToolSpec(tool_id, f"{tool_id} description"),) if tool_id else (),
        auto_mode=auto_mode,
    )
    return SystemSpec(
        meta=SystemMeta(name="stream-lifecycle"),
        defaults=None,
        graph=GraphNode(node=agent),
        hooks=HooksConfig(hooks=hooks),
    )


def _write_tool(base_dir: Path, tool_id: str) -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(
        f"def {tool_id}(message: str) -> str:\n    return 'sent: ' + message\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clear_hook_calls() -> None:
    fixtures.CALLS.clear()


async def _collect(engine: LangGraphEngine, message: str, **kwargs: Any) -> list[RunStreamEvent]:
    return [event async for event in engine.stream(message, **kwargs)]


# -- success path ------------------------------------------------------------


async def test_final_event_reports_accumulated_token_usage(tmp_path: Path) -> None:
    async with LangGraphEngine(tmp_path, model_factory=_usage_factory) as engine:
        await engine.build(_spec())
        events = await _collect(engine, "hello")

    final = next(e for e in events if e.type == "final")
    assert (final.input_tokens, final.output_tokens) == (11, 7)
    assert final.content == "ok"
    assert final.route == ("solo",)


async def test_absent_provider_usage_is_reported_as_none_not_zero(tmp_path: Path) -> None:
    async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
        await engine.build(_spec())
        events = await _collect(engine, "hello")

    final = next(e for e in events if e.type == "final")
    assert final.input_tokens is None
    assert final.output_tokens is None


async def test_successful_stream_ends_the_run_and_fires_end_hook(tmp_path: Path) -> None:
    async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
        await engine.build(_spec(HookSpec("on_run_end", f"{_FIX}:record_run_end")))
        await _collect(engine, "hello", context=RunContext(run_id="run-ok"))
        status = await engine.get_run_status("run-ok")

    assert status == "completed"
    summaries = [call[1] for call in fixtures.CALLS if call[0] == "on_run_end"]
    assert len(summaries) == 1
    assert summaries[0].status == "succeeded"
    assert summaries[0].visited == ("solo",)


async def test_start_hook_may_replace_the_context_the_run_is_registered_under(
    tmp_path: Path,
) -> None:
    # record_run_start returns a modified context; the rest of the run — including
    # registration and the end summary — must use what the hook returned.
    async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
        await engine.build(
            _spec(
                HookSpec("on_run_start", f"{_FIX}:record_run_start"),
                HookSpec("on_run_end", f"{_FIX}:record_run_end"),
            )
        )
        await _collect(engine, "hello", context=RunContext(run_id="run-hooked"))
        status = await engine.get_run_status("run-hooked")

    assert status == "completed"
    summary = next(call[1] for call in fixtures.CALLS if call[0] == "on_run_end")
    assert summary.run_id == "run-hooked"


# -- failure path ------------------------------------------------------------


async def test_graph_failure_reaches_the_consumer_and_fails_the_run(tmp_path: Path) -> None:
    async with LangGraphEngine(tmp_path, model_factory=_broken_factory) as engine:
        await engine.build(
            _spec(
                HookSpec("on_run_error", f"{_FIX}:record_run_error"),
                HookSpec("on_run_end", f"{_FIX}:record_run_end"),
            )
        )
        with pytest.raises(RuntimeError, match="model down"):
            await _collect(engine, "hello", context=RunContext(run_id="run-boom"))
        status = await engine.get_run_status("run-boom")

    assert status == "failed"
    points = [call[0] for call in fixtures.CALLS]
    assert "on_run_error" in points
    assert "on_run_end" not in points  # success-only


async def test_start_hook_failure_aborts_before_the_run_is_registered(tmp_path: Path) -> None:
    async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
        await engine.build(_spec(HookSpec("on_run_start", f"{_FIX}:boom")))
        with pytest.raises(Exception, match="boom"):
            await _collect(engine, "hello", context=RunContext(run_id="run-unstarted"))

        with pytest.raises(Exception, match="run-unstarted"):
            await engine.get_run_status("run-unstarted")


# -- suspended path ----------------------------------------------------------


async def test_pending_approval_event_leaves_the_run_open(tmp_path: Path) -> None:
    _write_tool(tmp_path, "send_email")
    async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
        await engine.build(
            _spec(
                HookSpec("on_run_end", f"{_FIX}:record_run_end"),
                tool_id="send_email",
                auto_mode=False,
            )
        )
        events = await _collect(engine, "hello", context=RunContext(run_id="run-pending"))
        status = await engine.get_run_status("run-pending")

    pending = next(e for e in events if e.type == "pending_approval")
    assert pending.tool_name == "send_email"
    assert pending.run_id == "run-pending"
    assert not any(e.type == "final" for e in events)
    # The run is suspended, not finished: it must stay resumable and unended.
    assert status == "pending_approval"
    assert not any(call[0] == "on_run_end" for call in fixtures.CALLS)


# -- ambient per-run state ---------------------------------------------------


async def test_context_vars_are_restored_after_a_completed_stream(tmp_path: Path) -> None:
    sentinel = StreamSinks()
    token = current_streams.set(sentinel)
    try:
        async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
            await engine.build(_spec())
            await _collect(engine, "hello")

        assert current_streams.get() is sentinel
        assert current_run_context.get() is None
        assert current_execution.get() is None
    finally:
        current_streams.reset(token)


async def test_context_vars_are_restored_when_the_consumer_walks_away(tmp_path: Path) -> None:
    sentinel = StreamSinks()
    token = current_streams.set(sentinel)
    try:
        async with LangGraphEngine(tmp_path, model_factory=_factory) as engine:
            await engine.build(_spec())
            # ``Engine.stream`` is typed as an iterator; the concrete engine
            # returns a generator, which is what a caller abandoning the stream
            # (a disconnected SSE client, a `break`) ends up closing.
            events = cast(AsyncGenerator[RunStreamEvent, None], engine.stream("hello"))
            await events.__anext__()  # consume one event, then abandon the stream
            await events.aclose()

            assert current_streams.get() is sentinel
            assert current_run_context.get() is None
            assert current_execution.get() is None
    finally:
        current_streams.reset(token)


async def test_abandoned_stream_stops_the_graph_and_cancels_the_run(tmp_path: Path) -> None:
    model = BlockingModel()

    def factory(*_: Any, **__: Any) -> BaseChatModel:
        return cast(BaseChatModel, cast(object, model))

    async with LangGraphEngine(tmp_path, model_factory=factory) as engine:
        await engine.build(_spec())
        events = cast(
            AsyncGenerator[RunStreamEvent, None],
            engine.stream("hello", context=RunContext(run_id="run-abandoned")),
        )
        async for event in events:
            if event.type == "answer_delta":
                break
        await events.aclose()

        model.released.set()
        await asyncio.sleep(0)

        assert model.resumed is False
        assert await engine.get_run_status("run-abandoned") == "cancelled"
