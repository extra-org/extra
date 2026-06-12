"""Translate a ``CompiledAgentGraph`` into a runnable LangGraph.

Topology
--------
- ``START`` → root agent node.
- Each **orchestrator** node records itself as visited; routing is decided by
  its conditional edge function (see ``_make_router``).
- Each **agent** (leaf) node calls its own model, optionally with tools bound,
  and runs a tool-call loop until the model stops requesting tools.

Routing
-------
``_make_router`` is the single place that decides which child to go to:

1. If ``agents_yml`` was provided and the orchestrator has a model → call the
   LLM with the orchestrator-prompt + child descriptions, structured output
   ``{"next": "<node_id>"}``.
2. Fallback → first declared child.

Tests inject a fake ``model_factory`` and pass ``agents_yml`` to enable LLM
routing without hitting the network.

Plugins & prompts
-----------------
All plugin loading (tools, resolvers) and prompt-file reading require
``agents_yml`` (the path to the agent YAML file).  When omitted, agents fall
back to their ``description`` as the system prompt and run without tools or
resolver context.

Per-node models (ADR 0006, ADR 0008)
-------------------------------------
Each node's resolved model config is fed to ``model_factory`` once at
graph-build time — production uses :func:`build_chat_model`; tests inject a
fake that stays offline.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from agentplatform.graph.models import (
    AgentDeclaration,
    AgentNode,
    CompiledAgentGraph,
    OrchestratorDeclaration,
)
from agentplatform.models import build_chat_model
from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.langchain_tool_adapter import (
    build_langchain_tools_from_runtime_tools,
    ensure_no_duplicate_tool_names,
)
from agentplatform.runtime.plugin_loader import PluginLoader
from agentplatform.runtime.state import GraphState
from agentplatform.runtime.tool_registry import ToolRegistry

ModelFactory = Callable[[str, str, float | None], BaseChatModel]


class _RouteDecision(BaseModel):
    """Structured output returned by the orchestrator's routing LLM call."""

    next: str
    """node_id of the child to route to."""


def build_langgraph(
    graph: CompiledAgentGraph,
    *,
    agents_yml: Path | None = None,
    model_factory: ModelFactory = build_chat_model,
    tool_registry: ToolRegistry | None = None,
) -> CompiledStateGraph:
    """Build and compile a LangGraph from the compiled agent graph.

    Parameters
    ----------
    graph:
        The compiled agent graph produced by ``compile_spec``.
    agents_yml:
        Path to the ``agents.yml`` file.  When provided, enables:
        - LLM-based orchestrator routing (using the orchestrator's model).
        - System-prompt loading from ``.md`` files.
        - Tool and resolver loading from ``plugins/``.
    model_factory:
        Builds a ``BaseChatModel`` from ``(provider, name, temperature)``.
        Override in tests.
    tool_registry:
        Optional runtime tool registry. When provided, agent nodes adapt
        MCP-backed RuntimeTool metadata into executable LangChain tools that
        delegate back through ToolRegistry.
    """
    loader = PluginLoader(agents_yml.parent) if agents_yml is not None else None
    builder = StateGraph(GraphState)

    for agent_node in graph.nodes_by_id.values():
        node = _make_node(
            agent_node,
            model_factory,
            loader,
            agents_yml,
            tool_registry=tool_registry,
        )
        builder.add_node(agent_node.node_path, node)  # type: ignore[call-overload]

    builder.add_edge(START, graph.root.node_path)

    for agent_node in graph.nodes_by_id.values():
        if agent_node.child_nodes:
            assert isinstance(agent_node.declaration, OrchestratorDeclaration)
            routes: dict[Hashable, str] = {}
            for child in agent_node.child_nodes:
                routes[child.node_path] = child.node_path

            builder.add_conditional_edges(
                agent_node.node_path,
                _make_router(agent_node, agent_node.declaration, model_factory, agents_yml),
                routes,
            )
        else:
            builder.add_edge(agent_node.node_path, END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def _make_node(
    agent_node: AgentNode,
    model_factory: ModelFactory,
    loader: PluginLoader | None,
    agents_yml: Path | None,
    *,
    tool_registry: ToolRegistry | None = None,
) -> Callable[[GraphState], dict[str, object]]:
    match agent_node.declaration:
        case OrchestratorDeclaration():
            node_path = agent_node.node_path

            def orchestrator_node(state: GraphState) -> dict[str, object]:
                return {"visited": [*state.get("visited", []), node_path]}

            return orchestrator_node

        case AgentDeclaration() as decl:
            return _make_agent_node(
                agent_node,
                decl,
                model_factory,
                loader,
                agents_yml,
                tool_registry=tool_registry,
            )

        case _:
            raise TypeError(f"Unknown declaration type: {type(agent_node.declaration)}")


def _make_agent_node(
    agent_node: AgentNode,
    declaration: AgentDeclaration,
    model_factory: ModelFactory,
    loader: PluginLoader | None,
    agents_yml: Path | None,
    *,
    tool_registry: ToolRegistry | None = None,
) -> Callable[[GraphState], dict[str, object]]:
    """Agent (leaf) node: resolves context, builds prompt, calls model in a loop."""
    node_path = agent_node.node_path

    # Build local Python tools once at graph-build time. MCP runtime tools need
    # the per-request ExecutionContext, so they are adapted inside the node.
    local_tools: list[BaseTool] = []
    if loader is not None:
        for resolved_tool in declaration.tools:
            local_tools.append(loader.load_tool(resolved_tool.id, resolved_tool.description))

    base_model: BaseChatModel = _build_node_model(declaration, model_factory)

    def node(state: GraphState) -> dict[str, object]:
        # Resolve context variables (called once per request, before LLM).
        ctx = ExecutionContext(message=state.get("message", ""), state=dict(state))
        context: dict[str, str] = {}

        if loader is not None:
            for resolved_resolver in declaration.resolvers:
                fn = loader.load_resolver(declaration.node_id, resolved_resolver.id)
                value = fn(ctx)
                ctx.resolved_context[resolved_resolver.id] = value
                context[resolved_resolver.id] = str(value)

        allowed_runtime_tools = []
        runtime_tools: list[BaseTool] = []
        if tool_registry is not None:
            bindings = tool_registry.get_tool_bindings_for_agent(declaration.node_id)
            # Local plugin tools are still bound through PluginLoader above. To
            # avoid double-binding local tools, this step adapts only
            # MCP-backed runtime tools while keeping execution source-agnostic
            # through ToolRegistry.call_tool.
            allowed_runtime_tools = [binding.tool for binding in bindings]
            mcp_runtime_tools = [
                binding.tool for binding in bindings if binding.provider_id == "mcp"
            ]
            ensure_no_duplicate_tool_names(
                agent_id=declaration.node_id,
                local_tools=local_tools,
                runtime_tools=mcp_runtime_tools,
            )
            runtime_tools = build_langchain_tools_from_runtime_tools(
                agent_id=declaration.node_id,
                runtime_tools=mcp_runtime_tools,
                tool_registry=tool_registry,
                ctx=ctx,
            )

        tools = [*local_tools, *runtime_tools]
        model = base_model.bind_tools(tools) if tools else base_model
        tool_map: dict[str, BaseTool] = {t.name: t for t in tools}

        system_prompt = _load_system_prompt(declaration, context, agents_yml)
        messages: list = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state.get("message", "")),
        ]

        # Tool-call loop: run until the model stops requesting tools.
        response = model.invoke(messages)
        while getattr(response, "tool_calls", None):
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool = tool_map.get(tool_name)
                if tool is None:
                    raise RuntimeError(
                        f"Agent '{declaration.node_id}' received unknown tool call '{tool_name}'."
                    )
                tool_result = tool.invoke(tc["args"])
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))
            response = model.invoke(messages)

        return {
            "visited": [*state.get("visited", []), node_path],
            "answer": _as_text(response.content),
            "allowed_tools": [tool.name for tool in allowed_runtime_tools],
        }

    return node


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def _make_router(
    agent_node: AgentNode,
    declaration: OrchestratorDeclaration,
    model_factory: ModelFactory,
    agents_yml: Path | None,
) -> Callable[[GraphState], str]:
    """Build the routing function for an orchestrator.

    Priority:
    1. ``agents_yml`` provided and orchestrator has a model → call LLM with
       structured output to pick the best child.
    2. First declared child (last-resort fallback).
    """
    first_child_id = agent_node.child_nodes[0].node_path
    valid_node_ids = {child.node_id for child in agent_node.child_nodes}
    child_lines: list[str] = []
    for child in agent_node.child_nodes:
        child_lines.append(f"- {child.node_id}: {child.declaration.description}")
    children_desc = "\n".join(child_lines)

    # Build the LLM routing chain once at graph-build time (only with agents_yml).
    routing_chain = None
    if (
        agents_yml is not None
        and declaration.model_provider is not None
        and declaration.model_name is not None
    ):
        routing_chain = model_factory(
            declaration.model_provider,
            declaration.model_name,
            declaration.model_temperature,
        ).with_structured_output(_RouteDecision)

    def route(state: GraphState) -> str:
        # 1. LLM routing.
        if routing_chain is not None:
            orchestrator_prompt = _load_orchestrator_prompt(declaration, agents_yml)
            system = (
                f"{orchestrator_prompt}\n\n"
                f"Available agents:\n{children_desc}\n\n"
                f"Respond with only the node_id of the best matching agent."
            )
            try:
                decision = routing_chain.invoke(
                    [
                        SystemMessage(content=system),
                        HumanMessage(content=state.get("message", "")),
                    ]
                )
                if isinstance(decision, _RouteDecision) and decision.next in valid_node_ids:
                    for child in agent_node.child_nodes:
                        if child.node_id == decision.next:
                            return child.node_path
            except Exception:  # routing failure → fall through to first-child fallback
                pass

        # 2. Fallback.
        return first_child_id

    return route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_orchestrator_prompt(
    declaration: OrchestratorDeclaration,
    agents_yml: Path | None,
) -> str:
    """Load the orchestrator routing instructions from its .md file."""
    if agents_yml is not None and declaration.orchestrator_prompt is not None:
        path = agents_yml.parent / declaration.orchestrator_prompt
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return declaration.description


def _load_system_prompt(
    declaration: AgentDeclaration,
    context: dict[str, str],
    agents_yml: Path | None,
) -> str:
    """Load and render the agent's system prompt, injecting resolver values."""
    if agents_yml is not None and declaration.system_prompt is not None:
        path = agents_yml.parent / declaration.system_prompt
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            for key, value in context.items():
                raw = raw.replace("{{ " + key + " }}", value)
                raw = raw.replace("{{" + key + "}}", value)
            return raw
    return declaration.description


def _build_node_model(declaration: AgentDeclaration, model_factory: ModelFactory) -> BaseChatModel:
    if declaration.model_provider is None or declaration.model_name is None:
        raise ValueError(
            f"Agent node '{declaration.node_id}' has no resolved model; "
            "declare a model on the node or a default under 'defaults.model'."
        )
    return model_factory(
        declaration.model_provider,
        declaration.model_name,
        declaration.model_temperature,
    )


def _as_text(content: object) -> str:
    """Normalise LangChain message content (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)
