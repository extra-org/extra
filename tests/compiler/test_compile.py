"""The compiler turns the validated example spec into a compiled graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentplatform.compiler import compile_spec
from agentplatform.graph import AgentDeclaration, CompiledAgentGraph, OrchestratorDeclaration
from agentplatform.spec import load_spec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "agents.yml"


@pytest.fixture
def graph() -> CompiledAgentGraph:
    return compile_spec(load_spec(EXAMPLE).spec)


def test_root_is_main_router(graph: CompiledAgentGraph) -> None:
    assert graph.system_name == "Rami Levy AI System"
    assert graph.root.node_id == "main_router"
    assert isinstance(graph.root.declaration, OrchestratorDeclaration)
    assert graph.root.parent_node_path is None


def test_tree_topology(graph: CompiledAgentGraph) -> None:
    child_ids = [child.node_id for child in graph.root.child_nodes]
    assert child_ids == ["flights_router", "super_agent", "admin_agent"]

    flights_router = graph.root.child_nodes[0]
    flight_child_ids = [child.node_id for child in flights_router.child_nodes]
    assert flight_child_ids == ["domestic_flights_agent", "international_flights_agent"]


def test_node_paths_are_full_paths(graph: CompiledAgentGraph) -> None:
    domestic = graph.nodes_by_id["main_router/flights_router/domestic_flights_agent"]
    assert domestic.node_id == "domestic_flights_agent"
    assert domestic.parent_node_path == "main_router/flights_router"


def test_references_resolve_to_specs(graph: CompiledAgentGraph) -> None:
    domestic = graph.nodes_by_id["main_router/flights_router/domestic_flights_agent"].declaration
    assert isinstance(domestic, AgentDeclaration)

    tool_ids = [tool.id for tool in domestic.tools]
    assert tool_ids == ["book_flight"]
    assert "flight" in domestic.tools[0].description.lower()

    assert [mcp.id for mcp in domestic.mcps] == ["flights_mcp"]
    assert domestic.mcps[0].url.endswith("/mcp/flights/sse")
    assert [r.id for r in domestic.resolvers] == ["current_date", "user_name"]


def test_model_defaults_and_overrides(graph: CompiledAgentGraph) -> None:
    # Agent with no model inherits the system default (haiku).
    domestic = graph.declarations_by_id["domestic_flights_agent"]
    assert isinstance(domestic, AgentDeclaration)
    assert domestic.model_name == "claude-haiku-4-5"

    # Orchestrator with its own model fully replaces the default (sonnet override).
    flights_router = graph.declarations_by_id["flights_router"]
    assert isinstance(flights_router, OrchestratorDeclaration)
    assert flights_router.model_name == "claude-sonnet-4-6"
    assert flights_router.model_temperature == 0.0


def test_protected_flag_preserved(graph: CompiledAgentGraph) -> None:
    admin = graph.declarations_by_id["admin_agent"]
    assert isinstance(admin, AgentDeclaration)
    assert admin.protected is True
