"""Compile a validated spec into an immutable ``CompiledAgentGraph``.

Input is the *validated* ``AgentEngineSpec`` (from the spec layer), never raw
YAML (ADR 0002). The compiler does two jobs:

1. Build one resolved ``NodeDeclaration`` per orchestrator/agent — references to
   resolvers/tools/MCPs become direct typed links, and the effective model is
   computed from defaults + node override.
2. Expand the ``graph`` tree into ``GraphInstance`` occurrences, each with a
   stable ``instance_id`` and a pointer back to its shared declaration
   (ADR 0006).

Validation (task 0002) already guarantees the input is well-formed: exactly one
graph root, every graph id is declared, all references resolve, and no cycles.
"""

from __future__ import annotations

from agentplatform.graph.models import (
    CompiledAgentGraph,
    GraphInstance,
    NodeDeclaration,
    ResolvedMcp,
    ResolvedResolver,
    ResolvedTool,
)
from agentplatform.spec.models import (
    AgentEngineSpec,
    AgentSpec,
    ModelSpec,
    OrchestratorSpec,
)


def compile_spec(spec: AgentEngineSpec) -> CompiledAgentGraph:
    """Compile a validated spec into an immutable compiled graph."""
    default_model = spec.defaults.model if spec.defaults else None
    declarations = _build_declarations(spec, default_model)

    root_id = next(iter(spec.graph))
    root = _build_instance(
        node_id=root_id,
        children=spec.graph[root_id],
        parent_instance_id=None,
        parent_path="",
        declarations=declarations,
    )

    instances_by_id: dict[str, GraphInstance] = {}
    _index_instances(root, instances_by_id)

    return CompiledAgentGraph(
        system_name=spec.system.name,
        root=root,
        instances_by_id=instances_by_id,
        declarations_by_id=declarations,
    )


def _build_declarations(
    spec: AgentEngineSpec,
    default_model: ModelSpec | None,
) -> dict[str, NodeDeclaration]:
    declarations: dict[str, NodeDeclaration] = {}
    for node_id, orchestrator in spec.orchestrators.items():
        declarations[node_id] = _declare_node(node_id, orchestrator, spec, default_model)
    for node_id, agent in spec.agents.items():
        declarations[node_id] = _declare_node(node_id, agent, spec, default_model)
    return declarations


def _declare_node(
    node_id: str,
    node_spec: OrchestratorSpec | AgentSpec,
    spec: AgentEngineSpec,
    default_model: ModelSpec | None,
) -> NodeDeclaration:
    return NodeDeclaration(
        node_id=node_id,
        node_type="agent" if isinstance(node_spec, AgentSpec) else "orchestrator",
        description=node_spec.description,
        model=node_spec.model or default_model,
        prompts=node_spec.prompts,
        resolvers=_resolve_resolvers(node_spec.resolvers),
        tools=(
            tuple(ResolvedTool(id=ref, spec=spec.tools[ref]) for ref in node_spec.tools)
            if isinstance(node_spec, AgentSpec) else ()
        ),
        mcps=(
            tuple(ResolvedMcp(id=ref, spec=spec.mcps[ref]) for ref in node_spec.mcps)
            if isinstance(node_spec, AgentSpec) else ()
        ),
        protected=node_spec.protected,
    )


def _resolve_resolvers(refs: list[str]) -> tuple[ResolvedResolver, ...]:
    return tuple(ResolvedResolver(id=ref) for ref in refs)


def _build_instance(
    *,
    node_id: str,
    children: object,
    parent_instance_id: str | None,
    parent_path: str,
    declarations: dict[str, NodeDeclaration],
) -> GraphInstance:
    path = f"{parent_path}/{node_id}" if parent_path else node_id
    declaration = declarations[node_id]

    child_instances = tuple(
        _build_instance(
            node_id=child_id,
            children=grandchildren,
            parent_instance_id=path,
            parent_path=path,
            declarations=declarations,
        )
        for child_id, grandchildren in _child_items(children)
    )

    return GraphInstance(
        instance_id=path,
        node_id=node_id,
        node_type=declaration.node_type,
        parent_instance_id=parent_instance_id,
        path=path,
        declaration=declaration,
        children=child_instances,
    )


def _child_items(children: object) -> list[tuple[str, object]]:
    """A graph node value is either ``None`` (leaf) or a mapping of children."""
    if isinstance(children, dict):
        return list(children.items())
    return []


def _index_instances(
    instance: GraphInstance,
    index: dict[str, GraphInstance],
) -> None:
    index[instance.instance_id] = instance
    for child in instance.children:
        _index_instances(child, index)
