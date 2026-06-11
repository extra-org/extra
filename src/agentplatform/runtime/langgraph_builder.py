"""Translate a ``CompiledAgentGraph`` into a runnable LangGraph.

Topology
--------
- ``START`` → root instance.
- Each **orchestrator** node records itself as visited; routing is decided by
  its conditional edge function (see ``_make_router``).
- Each **agent** (leaf) node calls its own model, optionally with tools bound,
  and runs a tool-call loop until the model stops requesting tools.

Routing
-------
``_make_router`` is the single place that decides which child to go to:

1. If ``route_hint`` in state matches a child → use it (test / manual override).
2. If ``base_dir`` was provided and the orchestrator has a model → call the LLM
   with the orchestrator-prompt + child descriptions, structured output
   ``{"next": "<node_id>"}``.
3. Fallback → first declared child.

This means tests that pass no ``base_dir`` always use the deterministic skeleton
(route_hint + first child) and never hit the network.  Production passes
``base_dir`` and gets real LLM routing.

Plugins & prompts
-----------------
All plugin loading (tools, resolvers) and prompt-file reading require
``base_dir`` (the directory that contains ``agents.yml``).  When ``base_dir``
is ``None``, agents fall back to their ``description`` as the system prompt and
run without tools or resolver context.

Per-node models (ADR 0006, ADR 0008)
-------------------------------------
Each node's resolved ``ModelSpec`` is fed to ``model_factory`` once at
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

from agentplatform.graph.models import CompiledAgentGraph, GraphInstance
from agentplatform.models import build_chat_model
from agentplatform.runtime.plugin_loader import PluginLoader
from agentplatform.runtime.state import GraphState
from agentplatform.spec.models import ModelSpec

ModelFactory = Callable[[ModelSpec], BaseChatModel]


class _RouteDecision(BaseModel):
    """Structured output returned by the orchestrator's routing LLM call."""

    next: str
    """node_id of the child to route to."""


def build_langgraph(
    graph: CompiledAgentGraph,
    *,
    base_dir: Path | None = None,
    model_factory: ModelFactory = build_chat_model,
) -> CompiledStateGraph:
    """Build and compile a LangGraph from the compiled agent graph.

    Parameters
    ----------
    graph:
        The compiled agent graph produced by ``compile_spec``.
    base_dir:
        Directory that contains ``agents.yml``.  When provided, enables:
        - LLM-based orchestrator routing (using the orchestrator's model).
        - System-prompt loading from ``.md`` files.
        - Tool and resolver loading from ``plugins/``.
    model_factory:
        Builds a ``BaseChatModel`` from a ``ModelSpec``.  Override in tests.
    """
    loader = PluginLoader(base_dir) if base_dir is not None else None
    builder = StateGraph(GraphState)

    for instance in graph.instances_by_id.values():
        node = _make_node(instance, model_factory, loader, base_dir)
        # LangGraph's add_node overloads don't resolve cleanly for a plain
        # Callable returning a partial-state dict; covered by runtime tests.
        builder.add_node(instance.instance_id, node)  # type: ignore[call-overload]

    builder.add_edge(START, graph.root.instance_id)

    for instance in graph.instances_by_id.values():
        if instance.children:
            routes: dict[Hashable, str] = {
                child.instance_id: child.instance_id for child in instance.children
            }
            builder.add_conditional_edges(
                instance.instance_id,
                _make_router(instance, model_factory, base_dir),
                routes,
            )
        else:
            builder.add_edge(instance.instance_id, END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def _make_node(
    instance: GraphInstance,
    model_factory: ModelFactory,
    loader: PluginLoader | None,
    base_dir: Path | None,
) -> Callable[[GraphState], dict[str, object]]:
    if instance.node_type == "agent":
        return _make_agent_node(instance, model_factory, loader, base_dir)
    # Orchestrator: just records itself as visited; routing is on the edge.
    def node(state: GraphState) -> dict[str, object]:
        return {"visited": [*state.get("visited", []), instance.instance_id]}

    return node


def _make_agent_node(
    instance: GraphInstance,
    model_factory: ModelFactory,
    loader: PluginLoader | None,
    base_dir: Path | None,
) -> Callable[[GraphState], dict[str, object]]:
    """Agent (leaf) node: resolves context, builds prompt, calls model in a loop."""
    # Build tools once at graph-build time.
    tools: list[BaseTool] = []
    if loader is not None:
        for resolved_tool in instance.declaration.tools:
            tools.append(loader.load_tool(resolved_tool.id, resolved_tool.spec.description))

    model: BaseChatModel = _build_node_model(instance, model_factory)
    if tools:
        model = model.bind_tools(tools)  # type: ignore[assignment]

    tool_map: dict[str, BaseTool] = {t.name: t for t in tools}

    def node(state: GraphState) -> dict[str, object]:
        # Resolve context variables (called once per request, before LLM).
        context: dict[str, str] = {}
        if loader is not None:
            for resolved_resolver in instance.declaration.resolvers:
                fn = loader.load_resolver(resolved_resolver.id)
                context[resolved_resolver.id] = str(fn())

        system_prompt = _load_system_prompt(instance, context, base_dir)
        messages: list = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state.get("message", "")),
        ]

        # Tool-call loop: run until the model stops requesting tools.
        response = model.invoke(messages)
        while getattr(response, "tool_calls", None):
            messages.append(response)
            for tc in response.tool_calls:
                tool_result = tool_map[tc["name"]].invoke(tc["args"])
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))
            response = model.invoke(messages)

        return {
            "visited": [*state.get("visited", []), instance.instance_id],
            "answer": _as_text(response.content),
        }

    return node


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def _make_router(
    instance: GraphInstance,
    model_factory: ModelFactory,
    base_dir: Path | None,
) -> Callable[[GraphState], str]:
    """Build the routing function for an orchestrator.

    Priority:
    1. ``route_hint`` in state matches a child → use it (tests / manual steering).
    2. ``base_dir`` provided and orchestrator has a model → call LLM with
       structured output to pick the best child.
    3. First declared child (last-resort fallback).
    """
    first_child_id = instance.children[0].instance_id
    valid_node_ids = {child.node_id for child in instance.children}
    children_desc = "\n".join(
        f"- {child.node_id}: {child.declaration.description}"
        for child in instance.children
    )

    # Build the LLM routing chain once at graph-build time (only with base_dir).
    routing_chain = None
    if base_dir is not None and instance.declaration.model is not None:
        routing_chain = model_factory(instance.declaration.model).with_structured_output(
            _RouteDecision
        )

    def route(state: GraphState) -> str:
        # 1. Explicit override — lets tests and callers steer without an LLM call.
        hint = state.get("route_hint", "")
        for child in instance.children:
            if hint in (child.node_id, child.instance_id):
                return child.instance_id

        # 2. LLM routing.
        if routing_chain is not None:
            orchestrator_prompt = _load_orchestrator_prompt(instance, base_dir)
            system = (
                f"{orchestrator_prompt}\n\n"
                f"Available agents:\n{children_desc}\n\n"
                f"Respond with only the node_id of the best matching agent."
            )
            try:
                decision = routing_chain.invoke([
                    SystemMessage(content=system),
                    HumanMessage(content=state.get("message", "")),
                ])
                if isinstance(decision, _RouteDecision) and decision.next in valid_node_ids:
                    for child in instance.children:
                        if child.node_id == decision.next:
                            return child.instance_id
            except Exception:  # routing failure → fall through to first-child fallback
                pass

        # 3. Fallback.
        return first_child_id

    return route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_orchestrator_prompt(instance: GraphInstance, base_dir: Path | None) -> str:
    """Load the orchestrator routing instructions from its .md file."""
    prompts = instance.declaration.prompts
    if base_dir is not None and prompts is not None and prompts.orchestrator is not None:
        path = base_dir / prompts.orchestrator
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return instance.declaration.description


def _load_system_prompt(
    instance: GraphInstance,
    context: dict[str, str],
    base_dir: Path | None,
) -> str:
    """Load and render the agent's system prompt, injecting resolver values."""
    prompts = instance.declaration.prompts
    if base_dir is not None and prompts is not None and prompts.system is not None:
        path = base_dir / prompts.system
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            for key, value in context.items():
                raw = raw.replace("{{ " + key + " }}", value)
                raw = raw.replace("{{" + key + "}}", value)
            return raw
    return instance.declaration.description


def _build_node_model(instance: GraphInstance, model_factory: ModelFactory) -> BaseChatModel:
    spec = instance.declaration.model
    if spec is None:
        raise ValueError(
            f"Agent node '{instance.instance_id}' has no resolved model; "
            "declare a model on the node or a default under 'defaults.model'."
        )
    return model_factory(spec)


def _as_text(content: object) -> str:
    """Normalise LangChain message content (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "".join(parts)
    return str(content)
