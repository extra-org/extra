"""Engine-level hook wiring: lifecycle points fire at the right moments.

Uses the engine's model_factory seam with a fake chat model (no LLM, no
network), mirroring tests/engine/test_engine_flow.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
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
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks.errors import HookLoadError
from agent_engine.runtime.hooks.models import RunContext
from tests.runtime.hooks import fixtures

_FIX = "tests.runtime.hooks.fixtures"
_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


class FakeChatModel:
    """Route through one tool (if any), then answer — same shape as flow tests."""

    def __init__(self, answer: str = "ok", tool_names: list[str] | None = None) -> None:
        self._answer = answer
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> FakeChatModel:
        return FakeChatModel(self._answer, [t.name for t in tools])

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        already = any(isinstance(m, ToolMessage) for m in messages)
        if self._tool_names and not already:
            return AIMessage(
                content="",
                tool_calls=[ToolCall(name=self._tool_names[0], args={"message": "x"}, id="c1")],
            )
        return AIMessage(content=self._answer)


@pytest.fixture
def model_factory() -> Callable[[str, str, float | None], BaseChatModel]:
    def factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        return cast(BaseChatModel, FakeChatModel())

    return factory


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    fixtures.CALLS.clear()


def _agent(node_id: str, *, tools: tuple[ToolSpec, ...] = ()) -> GraphNode:
    return GraphNode(
        node=AgentSpec(
            id=node_id,
            name=node_id,
            description=f"{node_id} agent",
            model=_MODEL,
            prompts=BasePromptSet(),
            tools=tools,
            auto_mode=True,  # execution/hook tests, not HITL approval
        )
    )


def _system(graph: GraphNode, *hooks: HookSpec) -> SystemSpec:
    return SystemSpec(
        meta=SystemMeta(name="hooks-system"),
        defaults=None,
        graph=graph,
        hooks=HooksConfig(hooks=hooks),
    )


def _system_with_default_hooks(graph: GraphNode) -> SystemSpec:
    return SystemSpec(meta=SystemMeta(name="hooks-system"), defaults=None, graph=graph)


def _write_tool(base_dir: Path, tool_id: str) -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(
        f"def {tool_id}(message: str) -> str:\n    return 'did: ' + message\n",
        encoding="utf-8",
    )


async def test_on_engine_start_runs_during_build(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"), HookSpec("on_engine_start", f"{_FIX}:record_engine_start"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        # Fires exactly once at build, before any run.
        assert [c[0] for c in fixtures.CALLS] == ["on_engine_start"]


async def test_on_run_start_runs_before_each_run(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"), HookSpec("on_run_start", f"{_FIX}:record_run_start"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        await engine.run("hi")
        await engine.run("again")
    assert [c[0] for c in fixtures.CALLS] == ["on_run_start", "on_run_start"]


async def test_run_start_receives_passed_context(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"), HookSpec("on_run_start", f"{_FIX}:record_run_start"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        await engine.run("hi", context=RunContext(user_id="bob", organization_id="org-1"))
    ctx = fixtures.CALLS[0][1]
    assert ctx.user_id == "bob"
    assert ctx.organization_id == "org-1"
    assert ctx.run_id is not None  # auto-generated when absent


async def test_on_run_error_runs_on_failure(tmp_path: Path, model_factory: Any) -> None:
    # A model factory that raises drives the run into failure.
    def boom_factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        class Boom:
            def bind_tools(self, tools: Any) -> Any:
                return self

            async def ainvoke(self, messages: Any) -> Any:
                raise RuntimeError("model down")

            async def astream(self, messages: Any) -> Any:
                raise RuntimeError("model down")
                yield  # pragma: no cover

        return cast(BaseChatModel, Boom())

    spec = _system(_agent("solo"), HookSpec("on_run_error", f"{_FIX}:record_run_error"))
    async with LangGraphEngine(tmp_path, model_factory=boom_factory) as engine:
        await engine.build(spec)
        with pytest.raises(RuntimeError):
            await engine.run("hi")
    assert [c[0] for c in fixtures.CALLS] == ["on_run_error"]


async def test_run_backwards_compatible_without_context(tmp_path: Path, model_factory: Any) -> None:
    # No hooks, no context arg — existing call shape still works.
    spec = _system(_agent("solo"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        result = await engine.run("hello")
    assert result.answer == "ok"
    assert result.visited == ["solo"]


async def test_engine_build_with_no_hooks_section_creates_empty_manager(
    tmp_path: Path, model_factory: Any
) -> None:
    spec = _system_with_default_hooks(_agent("solo"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        assert engine._hook_manager is not None
        assert engine._hook_manager.hook_count == 0
        result = await engine.run("hello")
    assert result.answer == "ok"


async def test_engine_build_with_empty_hooks_config_creates_empty_manager(
    tmp_path: Path, model_factory: Any
) -> None:
    spec = _system(_agent("solo"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        assert engine._hook_manager is not None
        assert engine._hook_manager.hook_count == 0
        result = await engine.run("hello")
    assert result.answer == "ok"


async def test_after_tool_call_no_hooks_does_not_alter_tool_usage(
    tmp_path: Path, model_factory: Any
) -> None:
    _write_tool(tmp_path, "local_tool")
    spec = _system(_agent("solo", tools=(ToolSpec("local_tool", "local"),)))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        result = await engine.run("call local_tool")

    assert [tool.name for tool in result.used_tools] == ["local_tool"]
    assert result.used_tools[0].provider == "local"
    assert result.used_tools[0].status == "succeeded"


async def test_hook_manager_built_once(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        mgr1 = engine._hook_manager
        await engine.run("a")
        await engine.run("b")
        assert engine._hook_manager is mgr1


async def test_bad_hook_ref_fails_build(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"), HookSpec("on_engine_start", "no.module:nope"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        with pytest.raises(HookLoadError) as exc:
            await engine.build(spec)
    # A bad ref aborts build before any request is served.
    assert exc.value.ref == "no.module:nope"


async def test_run_before_build_error_mentions_engine_lifecycle(
    tmp_path: Path, model_factory: Any
) -> None:
    engine = LangGraphEngine(tmp_path, model_factory=model_factory)
    with pytest.raises(RuntimeError, match="Engine must be built before running"):
        await engine.run("hi")


async def test_stream_before_build_error_mentions_engine_lifecycle(
    tmp_path: Path, model_factory: Any
) -> None:
    engine = LangGraphEngine(tmp_path, model_factory=model_factory)
    with pytest.raises(RuntimeError, match="Engine must be built before streaming"):
        async for _ in engine.stream("hi"):
            pass


async def test_no_hook_spec_builds_without_mcp_hook_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_factory: Any
) -> None:
    # A spec with an MCP server but no hooks must build with no auth injected
    # into the MCP client config (auth is only ever added by a before_mcp_request
    # hook, which this spec does not declare).
    captured_configs: list[dict[str, Any]] = []

    class FakeMultiServerMCPClient:
        def __init__(self, config: dict[str, dict[str, Any]]) -> None:
            captured_configs.extend(config.values())

        async def get_tools(self) -> list[Any]:
            return []

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        FakeMultiServerMCPClient,
    )

    config_path = tmp_path / "agents.yml"
    config_path.write_text(
        "system: {name: no-hooks}\n"
        "agents: {researcher: {description: d, mcps: [remote]}}\n"
        "graph: {researcher: }\n"
        "mcps: {remote: {url: 'https://mcp.example.com/mcp'}}\n",
        encoding="utf-8",
    )
    spec = YAMLParser().parse(str(config_path))
    assert spec.hooks.hooks == ()
    async with LangGraphEngine(config_path.parent, model_factory=model_factory) as engine:
        await engine.build(spec)
        assert engine._hook_manager is not None
        assert engine._hook_manager.hook_count == 0

    assert captured_configs
    assert all("auth" not in config for config in captured_configs)


async def test_on_run_end_runs_on_success(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"), HookSpec("on_run_end", f"{_FIX}:record_run_end"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        await engine.run("hi")
    summaries = [c[1] for c in fixtures.CALLS if c[0] == "on_run_end"]
    assert len(summaries) == 1
    assert summaries[0].status == "succeeded"
    assert summaries[0].run_id is not None
    assert summaries[0].visited == ("solo",)


async def test_on_run_end_does_not_run_on_failure(tmp_path: Path) -> None:
    def boom_factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        class Boom:
            def bind_tools(self, tools: Any) -> Any:
                return self

            async def ainvoke(self, messages: Any) -> Any:
                raise RuntimeError("model down")

            async def astream(self, messages: Any) -> Any:
                raise RuntimeError("model down")
                yield  # pragma: no cover

        return cast(BaseChatModel, Boom())

    spec = _system(
        _agent("solo"),
        HookSpec("on_run_end", f"{_FIX}:record_run_end"),
        HookSpec("on_run_error", f"{_FIX}:record_run_error"),
    )
    async with LangGraphEngine(tmp_path, model_factory=boom_factory) as engine:
        await engine.build(spec)
        with pytest.raises(RuntimeError):
            await engine.run("hi")
    points = [c[0] for c in fixtures.CALLS]
    assert "on_run_error" in points
    assert "on_run_end" not in points  # success-only


async def test_on_engine_stop_runs_on_close(tmp_path: Path, model_factory: Any) -> None:
    spec = _system(_agent("solo"), HookSpec("on_engine_stop", f"{_FIX}:record_engine_stop"))
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        assert not any(c[0] == "on_engine_stop" for c in fixtures.CALLS)  # not yet
    # close() ran on context exit.
    assert [c[0] for c in fixtures.CALLS if c[0] == "on_engine_stop"] == ["on_engine_stop"]


async def test_on_engine_stop_failure_does_not_block_cleanup(
    tmp_path: Path, model_factory: Any
) -> None:
    spec = _system(_agent("solo"), HookSpec("on_engine_stop", f"{_FIX}:boom"))
    engine = LangGraphEngine(tmp_path, model_factory=model_factory)
    await engine.build(spec)
    await engine.close()  # must not raise despite the failing stop hook
    assert engine._mcp_tools == {}
