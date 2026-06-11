"""The compiler turns the validated example spec into a compiled graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentplatform.compiler import compile_spec
from agentplatform.graph import CompiledAgentGraph
from agentplatform.spec import load_spec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "agents.yml"


@pytest.fixture
def graph() -> CompiledAgentGraph:
    return compile_spec(load_spec(EXAMPLE).spec)


def test_root_is_main_router(graph: CompiledAgentGraph) -> None:
    assert graph.system_name == "Rami Levy AI System"
    assert graph.root.node_id == "main_router"
    assert graph.root.node_type == "orchestrator"
    assert graph.root.parent_instance_id is None


def test_tree_topology(graph: CompiledAgentGraph) -> None:
    child_ids = [child.node_id for child in graph.root.children]
    assert child_ids == ["flights_router", "super_agent", "admin_agent"]

    flights_router = graph.root.children[0]
    flight_child_ids = [child.node_id for child in flights_router.children]
    assert flight_child_ids == ["domestic_flights_agent", "international_flights_agent"]


def test_instance_ids_are_full_paths(graph: CompiledAgentGraph) -> None:
    domestic = graph.instances_by_id["main_router/flights_router/domestic_flights_agent"]
    assert domestic.node_id == "domestic_flights_agent"
    assert domestic.parent_instance_id == "main_router/flights_router"


def test_references_resolve_to_specs(graph: CompiledAgentGraph) -> None:
    domestic = graph.instances_by_id[
        "main_router/flights_router/domestic_flights_agent"
    ].declaration

    tool_ids = [tool.id for tool in domestic.tools]
    assert tool_ids == ["book_flight"]
    assert "flight" in domestic.tools[0].spec.description.lower()

    assert [mcp.id for mcp in domestic.mcps] == ["flights_mcp"]
    assert domestic.mcps[0].spec.url.endswith("/mcp/flights/sse")
    assert [r.id for r in domestic.resolvers] == ["current_date", "user_name"]


def test_model_defaults_and_overrides(graph: CompiledAgentGraph) -> None:
    # Agent with no model inherits the system default (haiku).
    domestic = graph.declarations_by_id["domestic_flights_agent"]
    assert domestic.model is not None
    assert domestic.model.name == "claude-haiku-4-5"

    # Orchestrator with its own model fully replaces the default (sonnet override).
    flights_router = graph.declarations_by_id["flights_router"]
    assert flights_router.model is not None
    assert flights_router.model.name == "claude-sonnet-4-6"
    assert flights_router.model.temperature == 0.0


def test_protected_flag_preserved(graph: CompiledAgentGraph) -> None:
    assert graph.declarations_by_id["admin_agent"].protected is True
