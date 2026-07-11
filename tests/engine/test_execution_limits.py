"""Execution limits enforced end-to-end by the engine with fake models/tools.

No external MCP or LLM: a scripted fake model drives the tool/child loops, and a
local file-based tool stands in for real tools.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

from agent_engine.core.execution import ExecutionPolicy
from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    HooksConfig,
    ModelConfig,
    OrchestratorPromptSet,
    OrchestratorSpec,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


class ToolThenAnswerModel:
    """Emits up to ``n`` tool calls (args from ``args_fn(turn)``), then answers.

    ``turn`` is the number of tool results already in the message list, so this
    works whether prior calls executed or were blocked."""

    def __init__(
        self,
        n: int,
        args_fn: Callable[[int], dict[str, Any]],
        tool_names: list[str] | None = None,
    ) -> None:
        self._n = n
        self._args_fn = args_fn
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> ToolThenAnswerModel:
        return ToolThenAnswerModel(self._n, self._args_fn, [t.name for t in tools])

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        turn = sum(1 for m in messages if isinstance(m, ToolMessage))
        if self._tool_names and turn < self._n:
            return AIMessage(
                content="",
                tool_calls=[
                    ToolCall(name=self._tool_names[0], args=self._args_fn(turn), id=f"c{turn}")
                ],
            )
        return AIMessage(content="done")


class PlainAnswerModel:
    """Always answers, never calls a tool (used for child agents)."""

    def bind_tools(self, tools: list[Any]) -> PlainAnswerModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content="child-done")

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield AIMessage(content="child-done")


def _write_tool(base_dir: Path, tool_id: str = "echo") -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(
        f"def {tool_id}(message: str) -> str:\n    return 'did: ' + message\n",
        encoding="utf-8",
    )


def _agent_spec(node_id: str, **kw: Any) -> AgentSpec:
    # auto_mode=True: these tests cover execution limits, not HITL approval.
    kw.setdefault("auto_mode", True)
    return AgentSpec(
        id=node_id,
        name=node_id,
        description=f"{node_id} agent",
        model=_MODEL,
        prompts=BasePromptSet(),
        **kw,
    )


def _system(graph: GraphNode, policy: ExecutionPolicy) -> SystemSpec:
    return SystemSpec(
        meta=SystemMeta(name="exec-limits"),
        defaults=None,
        graph=graph,
        hooks=HooksConfig(),
        execution=policy,
    )


def _factory(model: BaseChatModel) -> Callable[[str, str, float | None], BaseChatModel]:
    def factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        return model

    return factory


async def _run_single_agent(tmp_path: Path, policy: ExecutionPolicy, model: Any) -> Any:
    _write_tool(tmp_path, "echo")
    spec = _system(
        GraphNode(node=_agent_spec("agent", tools=(ToolSpec("echo", "echo a message"),))),
        policy,
    )
    async with LangGraphEngine(tmp_path, model_factory=_factory(cast(BaseChatModel, model))) as eng:
        await eng.build(spec)
        return await eng.run("go")


async def test_normal_run_still_succeeds(tmp_path: Path) -> None:
    model = ToolThenAnswerModel(1, lambda i: {"message": f"m{i}"})
    result = await _run_single_agent(tmp_path, ExecutionPolicy(), model)
    assert result.answer == "done"
    assert len(result.used_tools) == 1  # one executed tool call


async def test_max_tool_calls_enforced(tmp_path: Path) -> None:
    # Model wants 6 calls; policy allows 3. Distinct args so duplicate rule is moot.
    policy = ExecutionPolicy(
        max_iterations=20,
        max_tool_calls=3,
        max_tool_calls_per_agent=99,
        max_child_agent_calls=8,
        allow_duplicate_tool_calls=True,
    )
    model = ToolThenAnswerModel(6, lambda i: {"message": f"m{i}"})
    result = await _run_single_agent(tmp_path, policy, model)
    assert len(result.used_tools) == 3  # blocked after the 3rd execution


async def test_max_tool_calls_per_agent_enforced(tmp_path: Path) -> None:
    policy = ExecutionPolicy(
        max_iterations=20,
        max_tool_calls=99,
        max_tool_calls_per_agent=2,
        max_child_agent_calls=8,
        allow_duplicate_tool_calls=True,
    )
    model = ToolThenAnswerModel(4, lambda i: {"message": f"m{i}"})
    result = await _run_single_agent(tmp_path, policy, model)
    assert len(result.used_tools) == 2


async def test_duplicate_tool_call_blocked(tmp_path: Path) -> None:
    # Same args twice, default policy (duplicates disabled): only the first runs.
    model = ToolThenAnswerModel(2, lambda i: {"message": "same"})
    result = await _run_single_agent(tmp_path, ExecutionPolicy(), model)
    assert len(result.used_tools) == 1


async def test_max_iterations_enforced(tmp_path: Path) -> None:
    # Model loops forever; only max_iterations stops it (proves no hang).
    policy = ExecutionPolicy(
        max_iterations=3,
        max_tool_calls=999,
        max_tool_calls_per_agent=999,
        max_child_agent_calls=8,
        allow_duplicate_tool_calls=True,
    )
    model = ToolThenAnswerModel(1000, lambda i: {"message": f"m{i}"})
    result = await _run_single_agent(tmp_path, policy, model)
    assert len(result.used_tools) == 3  # 3 iterations executed, then the loop stopped


async def test_max_child_agent_calls_enforced(tmp_path: Path) -> None:
    # Orchestrator wants 4 child calls; policy allows 2.
    orch_model = ModelConfig(provider="fake", name="orch", temperature=None)
    graph = GraphNode(
        node=OrchestratorSpec(
            id="root",
            name="root",
            description="router",
            model=orch_model,
            prompts=OrchestratorPromptSet(orchestrator="route"),
        ),
        children=(GraphNode(node=_agent_spec("child")),),
    )
    policy = ExecutionPolicy(
        max_iterations=20,
        max_tool_calls=99,
        max_tool_calls_per_agent=99,
        max_child_agent_calls=2,
        allow_duplicate_tool_calls=True,
    )

    orchestrator = ToolThenAnswerModel(4, lambda i: {"message": f"m{i}"})
    child = PlainAnswerModel()

    def factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        return cast(BaseChatModel, orchestrator if name == "orch" else child)

    async with LangGraphEngine(tmp_path, model_factory=factory) as eng:
        await eng.build(_system(graph, policy))
        result = await eng.run("go")

    assert result.visited.count("root/child") == 2  # only 2 child calls executed
