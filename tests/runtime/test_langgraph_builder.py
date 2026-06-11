"""The compiled graph builds a runnable LangGraph that routes and answers.

Uses a fake model factory so tests are deterministic and never hit a provider.
"""

from __future__ import annotations

from itertools import cycle
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agentplatform.compiler import compile_spec
from agentplatform.runtime import build_langgraph
from agentplatform.spec import load_spec
from agentplatform.spec.models import ModelSpec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "agents.yml"


class RecordingFactory:
    """A model factory that records the specs it is asked to build.

    Each model echoes its resolved provider:name, so an agent's answer reveals
    which model that node actually used.
    """

    def __init__(self) -> None:
        self.specs: list[ModelSpec] = []

    def __call__(self, spec: ModelSpec) -> GenericFakeChatModel:
        self.specs.append(spec)
        reply = AIMessage(content=f"answer from {spec.provider}:{spec.name}")
        return GenericFakeChatModel(messages=cycle([reply]))


@pytest.fixture
def factory() -> RecordingFactory:
    return RecordingFactory()


@pytest.fixture
def app(factory: RecordingFactory):
    graph = compile_spec(load_spec(EXAMPLE).spec)
    return build_langgraph(graph, model_factory=factory)


def test_routes_to_supermarket(app) -> None:
    result = app.invoke({"message": "I need milk", "route_hint": "super_agent"})

    assert result["visited"] == ["main_router", "main_router/super_agent"]
    # super_agent declares no model → inherits the system default (haiku).
    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"


def test_routes_deep_into_flights(app) -> None:
    # Hint steers main_router → flights_router; below that, no hint matches the
    # leaves so it falls back to the first child (domestic).
    result = app.invoke({"message": "book a flight", "route_hint": "flights_router"})

    assert result["visited"] == [
        "main_router",
        "main_router/flights_router",
        "main_router/flights_router/domestic_flights_agent",
    ]
    # domestic_flights_agent inherits default (haiku).
    assert result["answer"] == "answer from anthropic:claude-haiku-4-5"


def test_default_routing_follows_first_child(app) -> None:
    result = app.invoke({"message": "hello"})

    assert result["visited"][0] == "main_router"
    assert result["answer"].startswith("answer from ")


def test_each_agent_builds_its_own_resolved_model(factory: RecordingFactory) -> None:
    # Without base_dir: only agent (leaf) models are built — orchestrator LLM
    # routing is disabled, so no model is built for orchestrators.
    graph = compile_spec(load_spec(EXAMPLE).spec)
    build_langgraph(graph, model_factory=factory)

    built = {f"{s.provider}:{s.name}" for s in factory.specs}
    # All four agents inherit the default (haiku).
    assert built == {"anthropic:claude-haiku-4-5"}
    assert len(factory.specs) == 4  # domestic, international, super, admin
