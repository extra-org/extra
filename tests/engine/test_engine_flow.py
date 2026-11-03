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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel

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
from agent_engine.engine.langgraph.execution.node_executor import NodeExecutor
from agent_engine.engine.langgraph.filters import AccessFilter
from agent_engine.engine.langgraph.nodes.child_entry import ChildEntry
from agent_engine.engine.types import RunResult
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.runtime.hooks import AuthContext, RunContext
from agent_engine.runtime.state import GraphState
from agent_engine.tool_usage.context_provider import ToolUsageContextProvider
from agent_engine.tool_usage.in_memory import InMemoryToolUsageRepository
from agent_engine.tool_usage.tracker import ToolUsageTracker
from tests.fixtures.utils import fake_model_factory

# ---------------------------------------------------------------------------
# Fake chat model
# ---------------------------------------------------------------------------


@pytest.fixture
def model_factory() -> Callable[[str, str, float | None], BaseChatModel]:
    return fake_model_factory


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


def usage_context() -> ToolUsageContextProvider:
    return ToolUsageContextProvider(InMemoryToolUsageRepository())


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


class _UnusedNode(NodeExecutor):
    async def execute(self, state: GraphState) -> GraphState:
        return state


def _candidate(node_id: str, protected: bool) -> ChildEntry:
    return ChildEntry(
        id=node_id,
        name=node_id,
        protected=protected,
        node=_UnusedNode(),
    )


@dataclass(frozen=True)
class _FilterCandidate:
    id: str
    protected: bool


def test_access_filter_accepts_candidates_without_runtime_node_dependency(tmp_path: Path) -> None:
    write_access(tmp_path, allow=False)
    route_filter = AccessFilter(tmp_path)

    kept = route_filter.filter(
        {},
        [_FilterCandidate("public", False), _FilterCandidate("protected", True)],
    )

    assert kept == [_FilterCandidate("public", False)]


def test_access_filter_drops_denied_protected(tmp_path: Path) -> None:
    write_access(tmp_path, allow=False)
    f = AccessFilter(tmp_path)

    kept = f.filter({}, [_candidate("pub", False), _candidate("adm", True)])

    assert [c.id for c in kept] == ["pub"]


def test_access_filter_keeps_protected_when_allowed(tmp_path: Path) -> None:
    write_access(tmp_path, allow=True)
    f = AccessFilter(tmp_path)

    kept = f.filter({}, [_candidate("adm", True)])

    assert [c.id for c in kept] == ["adm"]


def test_graph_state_accepts_generic_run_context() -> None:
    state: GraphState = {
        "message": "hello",
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


async def test_orchestrator_fallback_model_execution(tmp_path: Path, model_factory: Any) -> None:
    fallback_model = ModelConfig(provider="fake", name="successful-fallback")
    model = ModelConfig(
        provider="fake",
        name="failing-primary",
        fallback=fallback_model,
    )
    spec = SystemSpec(
        meta=SystemMeta(name="orchestrator-fallback-test"),
        defaults=None,
        graph=GraphNode(
            node=OrchestratorSpec(
                id="root",
                name="root",
                description="root orchestrator",
                model=model,
                prompts=OrchestratorPromptSet(),
            ),
            children=(agent("child"),),
        ),
    )

    result = await run_message(spec, tmp_path, model_factory, "hello child")
    assert result.visited == ["root", "root/child"]


class CapturingModel:
    """Records the system prompt it was handed, and answers without routing."""

    def __init__(self) -> None:
        self.captured_prompt: Any = None

    def bind_tools(self, tools: list[Any]) -> Any:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        from langchain_core.messages import AIMessage

        self.captured_prompt = messages[0].content
        return AIMessage(content="done")


def _orchestrator_prompt(rendered: str) -> str:
    """The full system prompt an orchestrator sends: Extra default, its own
    rendered prompts, then the engine routing contract."""
    from agent_engine.engine.langgraph.nodes.orchestrator_node import _ORCHESTRATOR_CONTRACT
    from agent_engine.engine.langgraph.prompting import EXTRA_DEFAULT_SYSTEM_PROMPT

    return f"{EXTRA_DEFAULT_SYSTEM_PROMPT}\n\n{rendered}\n\n{_ORCHESTRATOR_CONTRACT.strip()}"


def write_resolver(base_dir: Path, node_id: str, body: str) -> None:
    resolvers_dir = base_dir / "plugins" / "resolvers"
    resolvers_dir.mkdir(parents=True, exist_ok=True)
    (resolvers_dir / f"{node_id}.py").write_text(body, encoding="utf-8")


async def test_orchestrator_loads_both_prompts(tmp_path: Path) -> None:
    from agent_engine.core.spec import OrchestratorPromptSet, OrchestratorSpec
    from agent_engine.engine.langgraph.nodes.orchestrator_node import OrchestratorNode

    # Write prompt files
    sys_path = tmp_path / "system.md"
    orch_path = tmp_path / "orchestrator.md"
    sys_path.write_text("System persona content", encoding="utf-8")
    orch_path.write_text("Orchestrator routing content", encoding="utf-8")

    # 1. Test both prompts loaded
    spec_both = OrchestratorSpec(
        id="orch",
        name="orch",
        description="orch desc",
        model=_MODEL,
        prompts=OrchestratorPromptSet(
            system="system.md",
            orchestrator="orchestrator.md",
        ),
    )
    model_both = CapturingModel()
    node_both = OrchestratorNode(
        spec=spec_both,
        node_path="root",
        model=cast(Any, model_both),
        children=[],
        filters=[],
        resolver_loader=ResolverLoader(tmp_path),
        base_dir=tmp_path,
        usage_context=usage_context(),
        usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
    )
    await node_both.execute({"message": "hi", "visited": []})
    expected_both = _orchestrator_prompt("System persona content\n\nOrchestrator routing content")
    assert model_both.captured_prompt == expected_both

    # 2. Test system prompt only (no orchestrator) — uses system as the base prompt.
    spec_sys = OrchestratorSpec(
        id="orch",
        name="orch",
        description="orch desc",
        model=_MODEL,
        prompts=OrchestratorPromptSet(
            system="system.md",
        ),
    )
    model_sys = CapturingModel()
    node_sys = OrchestratorNode(
        spec=spec_sys,
        node_path="root",
        model=cast(Any, model_sys),
        children=[],
        filters=[],
        resolver_loader=ResolverLoader(tmp_path),
        base_dir=tmp_path,
        usage_context=usage_context(),
        usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
    )
    await node_sys.execute({"message": "hi", "visited": []})
    expected_sys = _orchestrator_prompt("System persona content")
    assert model_sys.captured_prompt == expected_sys

    # 3. Test orchestrator prompt only (no system) —
    # description is used as base, then orchestrator is appended.
    spec_orch = OrchestratorSpec(
        id="orch",
        name="orch",
        description="orch desc",
        model=_MODEL,
        prompts=OrchestratorPromptSet(
            orchestrator="orchestrator.md",
        ),
    )
    model_orch = CapturingModel()
    node_orch = OrchestratorNode(
        spec=spec_orch,
        node_path="root",
        model=cast(Any, model_orch),
        children=[],
        filters=[],
        resolver_loader=ResolverLoader(tmp_path),
        base_dir=tmp_path,
        usage_context=usage_context(),
        usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
    )
    await node_orch.execute({"message": "hi", "visited": []})
    expected_orch = _orchestrator_prompt("orch desc\n\nOrchestrator routing content")
    assert model_orch.captured_prompt == expected_orch

    # 4. Test fallback to description
    spec_desc = OrchestratorSpec(
        id="orch",
        name="orch",
        description="orch desc",
        model=_MODEL,
        prompts=OrchestratorPromptSet(),
    )
    model_desc = CapturingModel()
    node_desc = OrchestratorNode(
        spec=spec_desc,
        node_path="root",
        model=cast(Any, model_desc),
        children=[],
        filters=[],
        resolver_loader=ResolverLoader(tmp_path),
        base_dir=tmp_path,
        usage_context=usage_context(),
        usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
    )
    await node_desc.execute({"message": "hi", "visited": []})
    expected_desc = _orchestrator_prompt("orch desc")
    assert model_desc.captured_prompt == expected_desc


async def test_orchestrator_prompts_interpolate_resolver_values(tmp_path: Path) -> None:
    """A router's prompt can depend on who is asking, not just on the file.

    Both of an orchestrator's prompt files are rendered against the same
    resolved values, while the engine's own contract text is left alone.
    """
    from agent_engine.core.spec import (
        OrchestratorPromptSet,
        OrchestratorSpec,
        ResolverSpec,
    )
    from agent_engine.engine.langgraph.nodes.orchestrator_node import OrchestratorNode

    (tmp_path / "system.md").write_text("Persona for {{audience}}", encoding="utf-8")
    (tmp_path / "orchestrator.md").write_text("Routing for {{audience}}", encoding="utf-8")
    write_resolver(
        tmp_path,
        "root",
        "class Resolver:\n    def audience(self, ctx: dict) -> str:\n        return 'an admin'\n",
    )

    model = CapturingModel()
    node = OrchestratorNode(
        spec=OrchestratorSpec(
            id="root",
            name="root",
            description="root desc",
            model=_MODEL,
            resolvers=(ResolverSpec(id="audience", scope="agent"),),
            prompts=OrchestratorPromptSet(system="system.md", orchestrator="orchestrator.md"),
        ),
        node_path="root",
        model=cast(Any, model),
        children=[],
        filters=[],
        resolver_loader=ResolverLoader(tmp_path),
        base_dir=tmp_path,
        usage_context=usage_context(),
        usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
    )

    await node.execute({"message": "hi", "visited": []})

    assert model.captured_prompt == _orchestrator_prompt(
        "Persona for an admin\n\nRouting for an admin"
    )


async def test_orchestrator_resolvers_run_on_every_call(tmp_path: Path) -> None:
    """Resolved per run, never cached — one caller's value must not be reused
    for the next, which is the whole point of resolving from the request."""
    from agent_engine.core.spec import (
        OrchestratorPromptSet,
        OrchestratorSpec,
        ResolverSpec,
    )
    from agent_engine.engine.langgraph.nodes.orchestrator_node import OrchestratorNode

    (tmp_path / "orchestrator.md").write_text("caller={{caller}}", encoding="utf-8")
    write_resolver(
        tmp_path,
        "root",
        "class Resolver:\n"
        "    def __init__(self) -> None:\n"
        "        self.calls = 0\n"
        "    def caller(self, ctx: dict) -> str:\n"
        "        self.calls += 1\n"
        "        return f'user-{self.calls}'\n",
    )

    loader = ResolverLoader(tmp_path)
    spec = OrchestratorSpec(
        id="root",
        name="root",
        description="root desc",
        model=_MODEL,
        resolvers=(ResolverSpec(id="caller", scope="agent"),),
        prompts=OrchestratorPromptSet(orchestrator="orchestrator.md"),
    )
    prompts = []
    for _ in range(2):
        model = CapturingModel()
        node = OrchestratorNode(
            spec=spec,
            node_path="root",
            model=cast(Any, model),
            children=[],
            filters=[],
            resolver_loader=loader,
            base_dir=tmp_path,
            usage_context=usage_context(),
            usage_tracker=ToolUsageTracker(InMemoryToolUsageRepository()),
        )
        await node.execute({"message": "hi", "visited": []})
        prompts.append(model.captured_prompt)

    assert "caller=user-1" in prompts[0]
    assert "caller=user-2" in prompts[1]


async def test_engine_wires_resolvers_into_orchestrator_prompts(tmp_path: Path) -> None:
    """The same thing through the real engine, so the node's dependency is
    proven to be wired by `build`, not only by a hand-constructed node."""
    from agent_engine.core.spec import (
        OrchestratorPromptSet,
        OrchestratorSpec,
        ResolverSpec,
    )

    (tmp_path / "orchestrator.md").write_text("Routing for {{audience}}", encoding="utf-8")
    write_resolver(
        tmp_path,
        "root",
        "class Resolver:\n    def audience(self, ctx: dict) -> str:\n        return 'an admin'\n",
    )

    root = GraphNode(
        node=OrchestratorSpec(
            id="root",
            name="root",
            description="root desc",
            model=_MODEL,
            resolvers=(ResolverSpec(id="audience", scope="agent"),),
            prompts=OrchestratorPromptSet(orchestrator="orchestrator.md"),
        ),
        children=(agent("solo"),),
    )

    captured: list[str] = []

    def capturing_factory(provider: str, name: str, temperature: float | None) -> Any:
        model = CapturingModel()

        async def ainvoke(messages: list[Any]) -> Any:
            captured.append(str(messages[0].content))
            return await CapturingModel.ainvoke(model, messages)

        model.ainvoke = ainvoke  # type: ignore[method-assign]
        return model

    await run_message(system(root), tmp_path, capturing_factory, "hi")

    assert any("Routing for an admin" in prompt for prompt in captured)
