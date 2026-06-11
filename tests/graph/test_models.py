"""The compiled-graph models hold data and are immutable."""

from __future__ import annotations

import dataclasses

import pytest

from agentplatform.graph import AgentDeclaration, AgentNode, CompiledAgentGraph


def _leaf_declaration() -> AgentDeclaration:
    return AgentDeclaration(
        node_id="super_agent",
        description="Handle supermarket orders.",
    )


def test_models_carry_their_data() -> None:
    declaration = _leaf_declaration()
    agent_node = AgentNode(
        node_path="main_router/super_agent",
        node_id="super_agent",
        parent_node_path="main_router",
        declaration=declaration,
        child_nodes=(),
    )
    graph = CompiledAgentGraph(
        system_name="Rami Levy AI System",
        root=agent_node,
        nodes_by_id={agent_node.node_path: agent_node},
        declarations_by_id={declaration.node_id: declaration},
    )

    assert graph.root.declaration is declaration
    assert graph.nodes_by_id["main_router/super_agent"].node_id == "super_agent"


def test_agent_nodes_are_immutable() -> None:
    agent_node = AgentNode(
        node_path="i1",
        node_id="super_agent",
        parent_node_path=None,
        declaration=_leaf_declaration(),
        child_nodes=(),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        agent_node.node_id = "changed"  # type: ignore[misc]
