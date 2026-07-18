"""Behaviour tests for the LangGraph supervisor flow.

The chat model is faked through the engine's ``model_factory`` seam, so no real
LLM or network is touched. The fake mimics an LLM that routes via tools and then
synthesises a fixed answer:

- if it has bound tools and the conversation has no tool result yet, it calls
  one tool (chosen by name match in the message, else the first);
- otherwise it returns its fixed text answer.

That single rule drives both orchestrators (children-as-tools) and agents
(real/MCP tools), so one fake exercises the whole tree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    ModelConfig,
    OrchestratorPromptSet,
    OrchestratorSpec,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.langgraph.filters import AccessFilter
from agent_engine.engine.types import RunResult
from agent_engine.runtime.hooks import AuthContext, RunContext
from agent_engine.runtime.state import GraphState

# ---------------------------------------------------------------------------
# Fake chat model
# ---------------------------------------------------------------------------


class FakeRunnableWithFallbacks:
    def __init__(self, primary: Any, fallbacks: list[Any]) -> None:
        self._primary = primary
        self._fallbacks = fallbacks

    async def ainvoke(self, messages: list[Any]) -> Any:
        try:
            return await self._primary.ainvoke(messages)
        except Exception:
            for fallback in self._fallbacks:
                try:
                    return await fallback.ainvoke(messages)
                except Exception:
                    continue
            raise

    async def astream(self, messages: list[Any]) -> AsyncIterator[Any]:
        try:
            async for chunk in self._primary.astream(messages):
                yield chunk
        except Exception:
            for fallback in self._fallbacks:
                try:
                    async for chunk in fallback.astream(messages):
                        yield chunk
                    return
                except Exception:
                    continue
            raise


class FailingChatModel:
    def bind_tools(self, tools: list[Any]) -> FailingChatModel:
        return self

    def with_fallbacks(
        self,
        fallbacks: list[Any],
        exceptions_to_handle: tuple[type[BaseException], ...] = (Exception,),
    ) -> Any:
        return FakeRunnableWithFallbacks(self, fallbacks)

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        raise RuntimeError("Model execution failed")

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        if False:
            yield AIMessage(content="")
        raise RuntimeError("Model stream failed")


class FakeChatModel:
    """A scriptless stand-in: route through one tool, then answer."""

    def __init__(self, answer: str = "ok", tool_names: list[str] | None = None) -> None:
        self._answer = answer
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> FakeChatModel:
        return FakeChatModel(self._answer, [t.name for t in tools])

    def with_fallbacks(
        self,
        fallbacks: list[Any],
        exceptions_to_handle: tuple[type[BaseException], ...] = (Exception,),
    ) -> Any:
        return FakeRunnableWithFallbacks(self, fallbacks)

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        already_called = any(isinstance(m, ToolMessage) for m in messages)
        if self._tool_names and not already_called:
            call = ToolCall(
                name=self._select(messages),
                args={"message": self._user_text(messages)},
                id="call_1",
            )
            return AIMessage(content="", tool_calls=[call])
        return AIMessage(content=self._answer)

    def _select(self, messages: list[Any]) -> str:
        text = self._user_text(messages).lower()
        for name in self._tool_names:
            if name.lower() in text:
                return name
        return self._tool_names[0]

    @staticmethod
    def _user_text(messages: list[Any]) -> str:
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                return str(m.content)
        return ""


@pytest.fixture
def model_factory() -> Callable[[str, str, float | None], BaseChatModel]:
    def factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
        if name == "failing-primary":
            return cast(BaseChatModel, FailingChatModel())
        if name == "successful-fallback":
            return cast(BaseChatModel, FakeChatModel(answer="recovered ok"))
        return cast(BaseChatModel, FakeChatModel())

    return factory


# ---------------------------------------------------------------------------
# Spec + plugin builders
# ---------------------------------------------------------------------------

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


def agent(
    node_id: str,
    *,
    tools: tuple[ToolSpec, ...] = (),
    protected: bool = False,
    auto_mode: bool = True,
) -> GraphNode:
    # These flow tests exercise routing/tool execution, not Human-in-the-Loop, so
    # they default to auto_mode=True (no approval interrupts) — the behavior an
    # agent had before HITL existed. Approval behavior is covered in
    # tests/approvals/.
    spec = AgentSpec(
        id=node_id,
        name=node_id,
        description=f"{node_id} agent",
        model=_MODEL,
        protected=protected,
        prompts=BasePromptSet(),
        tools=tools,
        auto_mode=auto_mode,
    )
    return GraphNode(node=spec)


def orchestrator(node_id: str, children: list[GraphNode]) -> GraphNode:
    spec = OrchestratorSpec(
        id=node_id,
        name=node_id,
        description=f"{node_id} orchestrator",
        model=_MODEL,
        prompts=OrchestratorPromptSet(),
    )
    return GraphNode(node=spec, children=tuple(children))


def system(graph: GraphNode) -> SystemSpec:
    return SystemSpec(meta=SystemMeta(name="test-system"), defaults=None, graph=graph)


def write_tool(base_dir: Path, tool_id: str) -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(
        f"def {tool_id}(message: str) -> str:\n    return 'did: ' + message\n",
        encoding="utf-8",
    )


def write_access(base_dir: Path, *, allow: bool) -> None:
    plugins = base_dir / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        f"        return {allow}\n",
        encoding="utf-8",
    )


def write_context_access(base_dir: Path) -> None:
    plugins = base_dir / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        "        allowed_nodes = ctx.get('metadata', {}).get('allowed_nodes', ())\n"
        "        groups = ctx.get('auth', {}).get('metadata', {}).get('groups', ())\n"
        "        has_no_raw_token = 'inbound_access_token' not in ctx.get('auth', {})\n"
        "        return node_id in allowed_nodes and 'docs' in groups and has_no_raw_token\n",
        encoding="utf-8",
    )


async def run_message(
    spec: SystemSpec,
    base_dir: Path,
    model_factory: Callable[[str, str, float | None], BaseChatModel],
    message: str,
    *,
    context: RunContext | None = None,
) -> RunResult:
    async with LangGraphEngine(base_dir, model_factory=model_factory) as engine:
        await engine.build(spec)
        return await engine.run(message, context=context)


# ---------------------------------------------------------------------------
# Flow tests
# ---------------------------------------------------------------------------


async def test_single_agent_answers(tmp_path: Path, model_factory: Any) -> None:
    result = await run_message(system(agent("solo")), tmp_path, model_factory, "hello")

    assert result.answer == "ok"
    assert result.visited == ["solo"]
    assert result.used_tools == ()


async def test_orchestrator_routes_to_matching_child(tmp_path: Path, model_factory: Any) -> None:
    spec = system(orchestrator("root", [agent("flights"), agent("super")]))

    result = await run_message(spec, tmp_path, model_factory, "please handle super order")

    assert result.visited == ["root", "root/super"]


async def test_nested_tool_usage_is_recorded(tmp_path: Path, model_factory: Any) -> None:
    # Regression for the supervisor used_tools merge: a tool called by a nested
    # agent must surface in the top-level trace.
    write_tool(tmp_path, "book_flight")
    spec = system(
        orchestrator("root", [agent("flights", tools=(ToolSpec("book_flight", "book"),))])
    )

    result = await run_message(spec, tmp_path, model_factory, "flights please")

    assert result.visited == ["root", "root/flights"]
    assert [t.name for t in result.used_tools] == ["book_flight"]
    assert result.used_tools[0].agent_id == "flights"
    assert result.used_tools[0].status == "succeeded"


async def test_protected_child_denied_is_unreachable(tmp_path: Path, model_factory: Any) -> None:
    write_access(tmp_path, allow=False)
    spec = system(orchestrator("root", [agent("public"), agent("admin", protected=True)]))

    result = await run_message(spec, tmp_path, model_factory, "go to admin please")

    # admin is filtered out, so the model can only reach public.
    assert "root/admin" not in result.visited
    assert result.visited == ["root", "root/public"]


async def test_protected_child_allowed_is_reachable(tmp_path: Path, model_factory: Any) -> None:
    write_access(tmp_path, allow=True)
    spec = system(orchestrator("root", [agent("admin", protected=True)]))

    result = await run_message(spec, tmp_path, model_factory, "admin task")

    assert result.visited == ["root", "root/admin"]


async def test_protected_child_can_be_allowed_by_run_context(
    tmp_path: Path, model_factory: Any
) -> None:
    write_context_access(tmp_path)
    spec = system(orchestrator("root", [agent("public"), agent("admin", protected=True)]))
    context = RunContext(
        user_id="u1",
        organization_id="org-1",
        metadata={"allowed_nodes": ("admin",), "department": "support"},
        auth_context=AuthContext(
            inbound_access_token="secret-token",
            metadata={"groups": ("docs",), "custom_policy_flag": True},
        ),
    )

    result = await run_message(spec, tmp_path, model_factory, "admin please", context=context)

    assert result.visited == ["root", "root/admin"]


async def test_protected_child_denied_when_run_context_missing_custom_data(
    tmp_path: Path, model_factory: Any
) -> None:
    write_context_access(tmp_path)
    spec = system(orchestrator("root", [agent("public"), agent("admin", protected=True)]))

    result = await run_message(
        spec,
        tmp_path,
        model_factory,
        "admin please",
        context=RunContext(metadata={"allowed_nodes": ("admin",)}),
    )

    assert result.visited == ["root", "root/public"]


def write_raising_access(base_dir: Path, *, exc: str) -> None:
    plugins = base_dir / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        f"        raise {exc}\n",
        encoding="utf-8",
    )


async def test_protected_child_hidden_when_resolver_raises(
    tmp_path: Path, model_factory: Any
) -> None:
    # Contract (docs/SIDECAR_CONTEXT_AUTH.md): if can_access returns false OR
    # RAISES, the node is hidden — the run must not fail open or crash.
    write_raising_access(tmp_path, exc="RuntimeError('policy backend down')")
    spec = system(orchestrator("root", [agent("public"), agent("admin", protected=True)]))

    result = await run_message(spec, tmp_path, model_factory, "go to admin please")

    assert "root/admin" not in result.visited
    assert result.visited == ["root", "root/public"]


async def test_unimplemented_resolver_stub_denies_instead_of_crashing(
    tmp_path: Path, model_factory: Any
) -> None:
    # A generated-but-unimplemented plugins/access.py raises NotImplementedError;
    # protected nodes must be denied, not take the whole run down.
    write_raising_access(tmp_path, exc="NotImplementedError")
    spec = system(orchestrator("root", [agent("public"), agent("admin", protected=True)]))

    result = await run_message(spec, tmp_path, model_factory, "admin task")

    assert result.visited == ["root", "root/public"]


async def test_stream_passes_run_context_to_access_filter(
    tmp_path: Path, model_factory: Any
) -> None:
    write_context_access(tmp_path)
    spec = system(orchestrator("root", [agent("public"), agent("admin", protected=True)]))
    context = RunContext(
        metadata={"allowed_nodes": ("admin",)},
        auth_context=AuthContext(metadata={"groups": ("docs",)}),
    )

    final_route: tuple[str, ...] = ()
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        async for ev in engine.stream("admin please", context=context):
            if ev.type == "final" and ev.route:
                final_route = ev.route

    assert final_route == ("root", "root/admin")


async def test_only_root_answer_is_streamed(tmp_path: Path, model_factory: Any) -> None:
    # Both nodes answer "ok"; if children also streamed we'd see "okok".
    spec = system(orchestrator("root", [agent("child")]))

    deltas: list[str] = []
    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        async for ev in engine.stream("hi child"):
            if ev.type == "answer_delta" and ev.content:
                deltas.append(ev.content)

    assert "".join(deltas) == "ok"


# ---------------------------------------------------------------------------
# AccessFilter boundary tests (security, fail-closed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    id: str
    protected: bool


def test_access_filter_drops_denied_protected(tmp_path: Path) -> None:
    write_access(tmp_path, allow=False)
    f = AccessFilter(tmp_path)

    kept = f.filter({}, [_Candidate("pub", False), _Candidate("adm", True)])

    assert [c.id for c in kept] == ["pub"]


def test_access_filter_keeps_protected_when_allowed(tmp_path: Path) -> None:
    write_access(tmp_path, allow=True)
    f = AccessFilter(tmp_path)

    kept = f.filter({}, [_Candidate("adm", True)])

    assert [c.id for c in kept] == ["adm"]


def test_graph_state_accepts_generic_run_context() -> None:
    state: GraphState = {
        "message": "hello",
        "used_tools": [],
        "run_context": {"metadata": {"allowed_nodes": ("admin",)}},
    }

    assert state["run_context"]["metadata"]["allowed_nodes"] == ("admin",)


# -- fallback execution tests --------------------------------------------------


async def test_fallback_model_execution(tmp_path: Path, model_factory: Any) -> None:
    # Configure an agent where the primary model fails and fallback succeeds
    fallback_model = ModelConfig(provider="fake", name="successful-fallback")
    model = ModelConfig(
        provider="fake",
        name="failing-primary",
        fallback=fallback_model,
    )
    spec = SystemSpec(
        meta=SystemMeta(name="fallback-test"),
        defaults=None,
        graph=GraphNode(
            node=AgentSpec(
                id="agent",
                name="agent",
                description="test agent",
                model=model,
                prompts=BasePromptSet(),
            )
        ),
    )

    result = await run_message(spec, tmp_path, model_factory, "hello")
    assert result.answer == "recovered ok"
    assert result.visited == ["agent"]


async def test_fallback_model_streaming(tmp_path: Path, model_factory: Any) -> None:
    fallback_model = ModelConfig(provider="fake", name="successful-fallback")
    model = ModelConfig(
        provider="fake",
        name="failing-primary",
        fallback=fallback_model,
    )
    spec = SystemSpec(
        meta=SystemMeta(name="fallback-test"),
        defaults=None,
        graph=GraphNode(
            node=AgentSpec(
                id="agent",
                name="agent",
                description="test agent",
                model=model,
                prompts=BasePromptSet(),
            )
        ),
    )

    async with LangGraphEngine(tmp_path, model_factory=model_factory) as engine:
        await engine.build(spec)
        events = [e async for e in engine.stream("hello")]

    assert any(e.type == "answer_delta" and e.content == "recovered ok" for e in events)
