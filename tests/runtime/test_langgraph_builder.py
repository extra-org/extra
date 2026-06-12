"""The compiled graph builds a runnable LangGraph that routes and answers.

Uses a fake model factory so tests are deterministic and never hit a provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import PrivateAttr

from agentplatform.compiler import compile_spec
from agentplatform.runtime import build_langgraph
from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.langgraph_builder import _RouteDecision
from agentplatform.runtime.tool_models import RuntimeTool, RuntimeToolBinding, ToolUsageRecord
from agentplatform.spec import load_spec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "agents.yml"


class FakeModel(BaseChatModel):
    """Fake chat model for tests.

    Returns a fixed reply for regular invocations.  When ``routing`` is set,
    ``with_structured_output(_RouteDecision)`` returns the configured target
    without touching a real LLM.
    """

    reply: str
    routing: str = ""  # target node_id to route to (empty = no routing override)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeModel:
        return self  # tools are ignored in tests — we test routing, not tool calls

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if schema is _RouteDecision and self.routing:
            target = self.routing
            return RunnableLambda(lambda _msgs: _RouteDecision(next=target))
        return super().with_structured_output(schema, **kwargs)


class RecordingFactory:
    """A model factory that records the configs it is asked to build.

    Each model echoes its resolved provider:name, so an agent's answer reveals
    which model that node actually used.

    ``routing`` is passed to every model so the fake routing LLM returns a
    deterministic decision without hitting a real provider.
    """

    def __init__(self, routing: str = "") -> None:
        self.calls: list[tuple[str, str, float | None]] = []
        self.routing = routing

    def __call__(self, provider: str, name: str, temperature: float | None) -> FakeModel:
        self.calls.append((provider, name, temperature))
        return FakeModel(reply=f"answer from {provider}:{name}", routing=self.routing)


class ToolCallingFakeModel(BaseChatModel):
    reply: str = "done"
    tool_call_name: str = ""
    _bound_tool_names: list[str] = PrivateAttr(default_factory=list)
    _invoke_count: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    @property
    def bound_tool_names(self) -> list[str]:
        return list(self._bound_tool_names)

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolCallingFakeModel:
        self._bound_tool_names = [tool.name for tool in tools]
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if schema is _RouteDecision:
            return RunnableLambda(lambda _msgs: _RouteDecision(next="flights_router"))
        return super().with_structured_output(schema, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.tool_call_name and self._invoke_count == 0:
            self._invoke_count += 1
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": self.tool_call_name,
                                    "args": {"origin": "TLV"},
                                    "id": "call-1",
                                }
                            ],
                        )
                    )
                ]
            )

        self._invoke_count += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class ToolCallingFactory:
    def __init__(self, tool_call_name: str = "") -> None:
        self.models: list[ToolCallingFakeModel] = []
        self.tool_call_name = tool_call_name

    def __call__(
        self,
        provider: str,
        name: str,
        temperature: float | None,
    ) -> ToolCallingFakeModel:
        model = ToolCallingFakeModel(reply=f"answer from {provider}:{name}")
        if len(self.models) == 0:
            model.tool_call_name = self.tool_call_name
        self.models.append(model)
        return model


class FakeRuntimeToolRegistry:
    def __init__(self, bindings: list[RuntimeToolBinding], *, fail: bool = False) -> None:
        self.bindings = bindings
        self.fail = fail
        self.calls: list[tuple[str, str, dict[str, object], ExecutionContext]] = []

    def get_tool_bindings_for_agent(self, agent_id: str) -> list[RuntimeToolBinding]:
        return list(self.bindings)

    def get_tools_for_agent(self, agent_id: str) -> list[RuntimeTool]:
        return [binding.tool for binding in self.bindings]

    async def call_tool(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        self.calls.append((agent_id, tool_name, arguments, ctx))
        if self.fail:
            raise RuntimeError("registry failed")
        return {"source": "registry", "tool": tool_name, "arguments": arguments}


@pytest.fixture
def plugin_base_dir(tmp_path: Path) -> Path:
    """Create minimal plugin stubs so PluginLoader doesn't raise FileNotFoundError."""
    tools_dir = tmp_path / "plugins" / "tools"
    resolvers_dir = tmp_path / "plugins" / "resolvers"
    tools_dir.mkdir(parents=True)
    resolvers_dir.mkdir(parents=True)

    for tool_id in ("book_flight", "add_to_cart"):
        (tools_dir / f"{tool_id}.py").write_text(f"def {tool_id}(**kwargs): return 'ok'\n")

    (resolvers_dir / "__init__.py").write_text('"""Resolvers."""\n')
    (resolvers_dir / "base.py").write_text(
        "from agentplatform.runtime import ExecutionContext\n\n"
        "class BaseResolver:\n"
        "    def current_date(self, ctx: ExecutionContext) -> str:\n"
        "        return 'current_date-value'\n"
        "    def user_name(self, ctx: ExecutionContext) -> str:\n"
        "        return 'user_name-value'\n"
    )
    (resolvers_dir / "domestic_flights_agent.py").write_text(
        "from agentplatform.runtime import ExecutionContext\n"
        "from plugins.resolvers.base import BaseResolver\n\n"
        "class DomesticFlightsAgentResolver(BaseResolver):\n"
        "    pass\n"
    )
    (resolvers_dir / "international_flights_agent.py").write_text(
        "from agentplatform.runtime import ExecutionContext\n"
        "from plugins.resolvers.base import BaseResolver\n\n"
        "class InternationalFlightsAgentResolver(BaseResolver):\n"
        "    pass\n"
    )
    (resolvers_dir / "super_agent.py").write_text(
        "from agentplatform.runtime import ExecutionContext\n"
        "from plugins.resolvers.base import BaseResolver\n\n"
        "class SuperAgentResolver(BaseResolver):\n"
        "    def subscription(self, ctx: ExecutionContext) -> str:\n"
        "        return 'subscription-value'\n"
    )
    (resolvers_dir / "resolvers.toml").write_text(
        '[resolvers]\nbase_class = "plugins.resolvers.base.BaseResolver"\n'
        "[resolvers.agents.domestic_flights_agent]\n"
        'class = "plugins.resolvers.domestic_flights_agent.DomesticFlightsAgentResolver"\n'
        "[resolvers.agents.international_flights_agent]\n"
        'class = "plugins.resolvers.international_flights_agent.'
        'InternationalFlightsAgentResolver"\n'
        "[resolvers.agents.super_agent]\n"
        'class = "plugins.resolvers.super_agent.SuperAgentResolver"\n'
    )

    return tmp_path


@pytest.fixture
def factory() -> RecordingFactory:
    return RecordingFactory()


@pytest.fixture
def app(factory: RecordingFactory):
    graph = compile_spec(load_spec(EXAMPLE).spec)
    return build_langgraph(graph, model_factory=factory)


def test_fallback_routing_follows_first_child(app) -> None:
    result = app.invoke({"message": "hello"})

    assert result["visited"] == [
        "main_router",
        "main_router/flights_router",
        "main_router/flights_router/domestic_flights_agent",
    ]
    assert result["answer"].startswith("answer from ")


def test_llm_routing_to_super_agent(plugin_base_dir: Path) -> None:
    # Provide base_dir so LLM routing is enabled; fake model routes to super_agent.
    factory = RecordingFactory(routing="super_agent")
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(graph, agents_yml=plugin_base_dir / "agents.yml", model_factory=factory)

    result = app.invoke({"message": "I need milk"})

    assert result["visited"] == ["main_router", "main_router/super_agent"]
    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"


def test_llm_routing_deep_into_flights(plugin_base_dir: Path) -> None:
    # Route main_router → flights_router; flights_router falls back to first child.
    factory = RecordingFactory(routing="flights_router")
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(graph, agents_yml=plugin_base_dir / "agents.yml", model_factory=factory)

    result = app.invoke({"message": "book a flight"})

    assert result["visited"] == [
        "main_router",
        "main_router/flights_router",
        "main_router/flights_router/domestic_flights_agent",
    ]
    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"


def test_each_agent_builds_its_own_resolved_model(factory: RecordingFactory) -> None:
    # Without base_dir: only agent (leaf) models are built — orchestrator LLM
    # routing is disabled, so no model is built for orchestrators.
    graph = compile_spec(load_spec(EXAMPLE).spec)
    build_langgraph(graph, model_factory=factory)

    built = {f"{provider}:{name}" for provider, name, _ in factory.calls}
    # All four agents inherit the default (haiku).
    assert built == {"anthropic:claude-haiku-4-5"}
    assert len(factory.calls) == 4  # domestic, international, super, admin


def test_agent_node_binds_local_and_mcp_runtime_tools(plugin_base_dir: Path) -> None:
    factory = ToolCallingFactory()
    runtime_tool = RuntimeTool(
        name="flights_search",
        description="Search flights through MCP",
        parameters_schema={"type": "object"},
    )
    registry = FakeRuntimeToolRegistry(
        [
            RuntimeToolBinding(
                tool=runtime_tool,
                provider_id="mcp",
                internal_tool_name="flights_search",
                server_id="flights_mcp",
            )
        ]
    )
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=factory,
        tool_registry=registry,  # type: ignore[arg-type]
    )

    app.invoke({"message": "book a flight"})

    # The selected domestic agent keeps its existing local book_flight tool and
    # additionally receives the MCP runtime tool adapted through ToolRegistry.
    domestic_model = factory.models[0]
    assert domestic_model.bound_tool_names == ["book_flight", "flights_search"]


def test_mcp_tool_invocation_delegates_through_tool_registry(plugin_base_dir: Path) -> None:
    factory = ToolCallingFactory(tool_call_name="flights_search")
    runtime_tool = RuntimeTool(
        name="flights_search",
        description="Search flights through MCP",
        parameters_schema={
            "type": "object",
            "properties": {"origin": {"type": "string"}},
        },
    )
    registry = FakeRuntimeToolRegistry(
        [
            RuntimeToolBinding(
                tool=runtime_tool,
                provider_id="mcp",
                internal_tool_name="flights_search",
                server_id="flights_mcp",
            )
        ]
    )
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=factory,
        tool_registry=registry,  # type: ignore[arg-type]
    )

    result = app.invoke({"message": "book a flight"})

    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"
    assert len(registry.calls) == 1
    agent_id, tool_name, arguments, ctx = registry.calls[0]
    assert agent_id == "domestic_flights_agent"
    assert tool_name == "flights_search"
    assert arguments == {"origin": "TLV"}
    assert ctx.message == "book a flight"
    assert result["used_tools"] == [
        ToolUsageRecord(
            name="flights_search",
            provider="mcp",
            status="succeeded",
            agent_id="domestic_flights_agent",
            server_id="flights_mcp",
        )
    ]


def test_local_tool_usage_is_recorded(plugin_base_dir: Path) -> None:
    factory = ToolCallingFactory(tool_call_name="book_flight")
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=factory,
    )

    result = app.invoke({"message": "book a flight"})

    assert result["used_tools"] == [
        ToolUsageRecord(
            name="book_flight",
            provider="local",
            status="succeeded",
            agent_id="domestic_flights_agent",
        )
    ]


def test_failed_mcp_tool_usage_is_recorded_before_error(plugin_base_dir: Path) -> None:
    factory = ToolCallingFactory(tool_call_name="flights_search")
    runtime_tool = RuntimeTool(
        name="flights_search",
        description="Search flights through MCP",
        parameters_schema={
            "type": "object",
            "properties": {"origin": {"type": "string"}},
        },
    )
    registry = FakeRuntimeToolRegistry(
        [
            RuntimeToolBinding(
                tool=runtime_tool,
                provider_id="mcp",
                internal_tool_name="flights_search",
                server_id="flights_mcp",
            )
        ],
        fail=True,
    )
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=factory,
        tool_registry=registry,  # type: ignore[arg-type]
    )
    used_tools: list[ToolUsageRecord] = []

    with pytest.raises(RuntimeError, match="registry failed"):
        app.invoke({"message": "book a flight", "used_tools": used_tools})

    assert used_tools == [
        ToolUsageRecord(
            name="flights_search",
            provider="mcp",
            status="failed",
            agent_id="domestic_flights_agent",
            server_id="flights_mcp",
            error=(
                "Tool 'flights_search' failed for agent 'domestic_flights_agent': registry failed"
            ),
        )
    ]


def test_tool_usage_is_not_inferred_from_answer_text(plugin_base_dir: Path) -> None:
    class ToolMentionFactory(ToolCallingFactory):
        def __call__(
            self,
            provider: str,
            name: str,
            temperature: float | None,
        ) -> ToolCallingFakeModel:
            model = ToolCallingFakeModel(reply="I used ask_question [mcp: deepwiki] succeeded")
            self.models.append(model)
            return model

    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=ToolMentionFactory(),
    )

    result = app.invoke({"message": "book a flight"})

    assert "ask_question" in result["answer"]
    assert result.get("used_tools", []) == []


def test_agent_without_mcp_behaves_as_before(plugin_base_dir: Path) -> None:
    factory = ToolCallingFactory()
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=factory,
    )

    result = app.invoke({"message": "book a flight"})

    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"
    assert factory.models[0].bound_tool_names == ["book_flight"]


def test_duplicate_local_and_mcp_tool_names_fail_clearly(plugin_base_dir: Path) -> None:
    factory = ToolCallingFactory()
    runtime_tool = RuntimeTool(
        name="book_flight",
        description="Conflicting MCP tool",
        parameters_schema={"type": "object"},
    )
    registry = FakeRuntimeToolRegistry(
        [
            RuntimeToolBinding(
                tool=runtime_tool,
                provider_id="mcp",
                internal_tool_name="book_flight",
                server_id="flights_mcp",
            )
        ]
    )
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(
        graph,
        agents_yml=plugin_base_dir / "agents.yml",
        model_factory=factory,
        tool_registry=registry,  # type: ignore[arg-type]
    )

    with pytest.raises(
        RuntimeError,
        match="Agent 'domestic_flights_agent' cannot bind duplicate tool name 'book_flight'",
    ):
        app.invoke({"message": "book a flight"})
