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

from agentplatform.compiler import compile_spec
from agentplatform.runtime import build_langgraph
from agentplatform.runtime.langgraph_builder import _RouteDecision
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


@pytest.fixture
def plugin_base_dir(tmp_path: Path) -> Path:
    """Create minimal plugin stubs so PluginLoader doesn't raise FileNotFoundError."""
    tools_dir = tmp_path / "plugins" / "tools"
    resolvers_dir = tmp_path / "plugins" / "resolvers"
    tools_dir.mkdir(parents=True)
    resolvers_dir.mkdir(parents=True)

    for tool_id in ("book_flight", "add_to_cart"):
        (tools_dir / f"{tool_id}.py").write_text(f"def {tool_id}(**kwargs): return 'ok'\n")

    for resolver_id in ("current_date", "user_name", "subscription"):
        (resolvers_dir / f"{resolver_id}.py").write_text(
            f"def {resolver_id}(): return '{resolver_id}-value'\n"
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
    app = build_langgraph(graph, base_dir=plugin_base_dir, model_factory=factory)

    result = app.invoke({"message": "I need milk"})

    assert result["visited"] == ["main_router", "main_router/super_agent"]
    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"


def test_llm_routing_deep_into_flights(plugin_base_dir: Path) -> None:
    # Route main_router → flights_router; flights_router falls back to first child.
    factory = RecordingFactory(routing="flights_router")
    graph = compile_spec(load_spec(EXAMPLE).spec)
    app = build_langgraph(graph, base_dir=plugin_base_dir, model_factory=factory)

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
