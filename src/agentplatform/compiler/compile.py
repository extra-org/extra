"""Compile a validated spec into an immutable ``CompiledAgentGraph``.

Input is the *validated* ``AgentEngineSpec`` (from the spec layer), never raw
YAML (ADR 0002). The compiler does two jobs:

1. Build one resolved ``NodeDeclaration`` per orchestrator/agent — spec types
   are flattened into graph-native fields, references resolved, and the
   effective model computed from defaults + node override.
2. Expand the ``graph`` tree into ``AgentNode`` objects, each with a stable
   ``node_path`` and a pointer back to its shared declaration
   (ADR 0006).

Validation (task 0002) already guarantees the input is well-formed: exactly
one graph root, every graph id is declared, all references resolve, no cycles.
"""

from __future__ import annotations

from agentplatform.graph.models import (
    AgentDeclaration,
    AgentNode,
    CompiledAgentGraph,
    NodeDeclaration,
    OrchestratorDeclaration,
    ResolvedMcp,
    ResolvedResolver,
    ResolvedTool,
)
from agentplatform.spec.models import (
    AgentEngineSpec,
    AgentSpec,
    GraphChildren,
    ModelSpec,
    OrchestratorSpec,
)


def compile_spec(spec: AgentEngineSpec) -> CompiledAgentGraph:
    """Compile a validated spec into an immutable compiled graph."""
    default_model = spec.defaults.model if spec.defaults else None
    declarations = _build_declarations(spec, default_model)

    root_id, root_children = next(iter(spec.graph.items()))
    root = _expand_node(
        node_id=root_id,
        children=root_children,
        parent_path=None,
        declarations=declarations,
    )

    nodes_by_id: dict[str, AgentNode] = {}
    _index_nodes(root, nodes_by_id)

    return CompiledAgentGraph(
        system_name=spec.system.name,
        root=root,
        nodes_by_id=nodes_by_id,
    )


def _build_declarations(
    spec: AgentEngineSpec,
    default_model: ModelSpec | None,
) -> dict[str, NodeDeclaration]:
    declarations: dict[str, NodeDeclaration] = {}
    for node_id, o in spec.orchestrators.items():
        declarations[node_id] = _compile_orchestrator(node_id, o, default_model)
    for node_id, a in spec.agents.items():
        declarations[node_id] = _compile_agent(node_id, a, spec, default_model)
    return declarations


def _compile_orchestrator(
    node_id: str,
    spec: OrchestratorSpec,
    default_model: ModelSpec | None,
) -> OrchestratorDeclaration:
    provider, name, temperature = _resolve_model(spec.model, default_model)

    resolvers: list[ResolvedResolver] = []
    for r in spec.resolvers:
        resolvers.append(ResolvedResolver(id=r))

    return OrchestratorDeclaration(
        node_id=node_id,
        description=spec.description,
        model_provider=provider,
        model_name=name,
        model_temperature=temperature,
        orchestrator_prompt=spec.prompts.orchestrator,
        system_prompt=spec.prompts.system,
        user_prompt=spec.prompts.user,
        resolvers=tuple(resolvers),
        protected=spec.protected,
    )


def _compile_agent(
    node_id: str,
    spec: AgentSpec,
    engine_spec: AgentEngineSpec,
    default_model: ModelSpec | None,
) -> AgentDeclaration:
    provider, name, temperature = _resolve_model(spec.model, default_model)

    resolvers: list[ResolvedResolver] = []
    for r in spec.resolvers:
        resolvers.append(ResolvedResolver(id=r))

    tools: list[ResolvedTool] = []
    for ref in spec.tools:
        tools.append(ResolvedTool(id=ref, description=engine_spec.tools[ref].description))

    mcps: list[ResolvedMcp] = []
    for ref in spec.mcps:
        mcps.append(ResolvedMcp(id=ref, url=engine_spec.mcps[ref].url))

    return AgentDeclaration(
        node_id=node_id,
        description=spec.description,
        model_provider=provider,
        model_name=name,
        model_temperature=temperature,
        system_prompt=spec.prompts.system if spec.prompts else None,
        user_prompt=spec.prompts.user if spec.prompts else None,
        resolvers=tuple(resolvers),
        tools=tuple(tools),
        mcps=tuple(mcps),
        protected=spec.protected,
    )


def _resolve_model(
    node_model: ModelSpec | None,
    default_model: ModelSpec | None,
) -> tuple[str | None, str | None, float | None]:
    """Return (provider, name, temperature) from the effective model, or all None."""
    m = node_model or default_model
    if m is None:
        return None, None, None
    return m.provider, m.name, m.temperature


def _expand_node(
    *,
    node_id: str,
    children: GraphChildren | None,
    parent_path: str | None,
    declarations: dict[str, NodeDeclaration],
) -> AgentNode:
    """Recursively expand a spec graph node into an ``AgentNode`` tree.

    Computes a stable ``node_path`` (e.g. ``main_router/super_agent``),
    looks up the pre-compiled declaration, and recurses into children.
    """
    node_path = f"{parent_path}/{node_id}" if parent_path else node_id

    child_nodes: list[AgentNode] = []
    for child_id, grandchildren in (children or {}).items():
        child_nodes.append(_expand_node(
            node_id=child_id,
            children=grandchildren,
            parent_path=node_path,
            declarations=declarations,
        ))

    return AgentNode(
        node_path=node_path,
        declaration=declarations[node_id],
        child_nodes=tuple(child_nodes),
    )


def _index_nodes(
    node: AgentNode,
    index: dict[str, AgentNode],
) -> None:
    index[node.node_path] = node
    for child in node.child_nodes:
        _index_nodes(child, index)
