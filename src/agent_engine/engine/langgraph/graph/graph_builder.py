"""Compile a validated graph specification into executable LangGraph nodes."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.tool_execution_manager import ToolExecutionManager
from agent_engine.core.spec import AgentSpec, BaseModelConfig, GraphNode, OrchestratorSpec
from agent_engine.core.spec import ModelConfig as NodeModelConfig
from agent_engine.engine.langgraph.checkpointing import CheckpointerHandle
from agent_engine.engine.langgraph.filters import RouteFilter
from agent_engine.engine.langgraph.graph.traversal import node_id
from agent_engine.engine.langgraph.nodes.agent_node import AgentNode
from agent_engine.engine.langgraph.nodes.child_entry import ChildEntry
from agent_engine.engine.langgraph.nodes.orchestrator_node import OrchestratorNode
from agent_engine.engine.langgraph.tools.agent_tool_binding import AgentToolBinding
from agent_engine.engine.langgraph.tools.tool_invoker import ToolInvoker
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.runtime.hooks import HookManager
from agent_engine.runtime.state import GraphState
from agent_engine.tool_usage.context_provider import ToolUsageContextProvider
from agent_engine.tool_usage.tracker import ToolUsageTracker

ModelFactory = Callable[..., BaseChatModel]
_MODEL_FACTORY_OPTIONAL_KWARGS = ("region", "max_tokens", "top_p")

# noinspection PyTypeChecker
RunGraphBuilder = StateGraph[GraphState, None, GraphState, GraphState]
# noinspection PyTypeChecker
RunGraph = CompiledStateGraph[GraphState, None, GraphState, GraphState]


def build_model(
    factory: ModelFactory,
    model: BaseModelConfig,
    overrides: dict[str, object] | None = None,
) -> BaseChatModel:
    """The one call shape every model-construction site in this codebase uses.

    `overrides` wins over `model`'s own value for a key — a caller's per-call
    need, e.g. a bounded output length for one completion rather than the
    model's own configured size.
    """
    return factory(
        model.provider,
        model.name,
        model.temperature,
        **_model_factory_kwargs(factory, model, overrides),
    )


def _model_factory_kwargs(
    factory: ModelFactory,
    model: BaseModelConfig,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """The optional kwargs a factory call for `model` should carry.

    Filtered by the factory's signature, so a narrower test factory never
    receives a kwarg it doesn't declare.
    """
    optional = {key: getattr(model, key) for key in _MODEL_FACTORY_OPTIONAL_KWARGS}
    optional.update(overrides or {})
    present: dict[str, object] = {
        key: value for key, value in optional.items() if value is not None
    }
    if not present:
        return {}
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return present
    parameters = signature.parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return present
    accepted = set(signature.parameters)
    return {key: value for key, value in present.items() if key in accepted}


class GraphBuilder:
    """Assemble node objects, wire explicit execution methods, and compile.

    All inputs are startup-scoped collaborators. The builder never owns request
    state and is discarded after producing the immutable compiled graph.
    """

    def __init__(
        self,
        *,
        base_dir: Path,
        model_factory: ModelFactory,
        filters: Sequence[RouteFilter],
        mcp_tools: Mapping[str, list[BaseTool]],
        tool_loader: ToolLoader,
        resolver_loader: ResolverLoader,
        hook_manager: HookManager,
        checkpointer: CheckpointerHandle,
        execution_manager: ToolExecutionManager,
        approval_coordinator: ApprovalCoordinator,
        usage_tracker: ToolUsageTracker,
        usage_context: ToolUsageContextProvider,
        system_namespace: str,
    ) -> None:
        self._base_dir = base_dir
        self._model_factory = model_factory
        self._filters = list(filters)
        self._mcp_tools = mcp_tools
        self._tool_loader = tool_loader
        self._resolver_loader = resolver_loader
        self._hook_manager = hook_manager
        self._checkpointer = checkpointer
        self._execution_manager = execution_manager
        self._approval_coordinator = approval_coordinator
        self._usage_tracker = usage_tracker
        self._usage_context = usage_context
        self._system_namespace = system_namespace

    # noinspection PyTypeChecker
    def compile(self, root: GraphNode) -> RunGraph:
        """Build and compile the runtime graph from a typed root node."""
        builder = StateGraph(GraphState)
        self._wire_node(builder, root, parent_path=None)
        builder.add_edge(START, node_id(root, parent_path=None))
        return builder.compile(checkpointer=self._checkpointer.saver)

    # noinspection PyTypeChecker
    def _wire_node(
        self,
        builder: RunGraphBuilder,
        node: GraphNode,
        parent_path: str | None,
    ) -> None:
        path = node_id(node, parent_path)
        if isinstance(node.node, OrchestratorSpec):
            orchestrator_node = self._build_orchestrator_node(node, parent_path)
            builder.add_node(path, orchestrator_node.execute)
        else:
            assert isinstance(node.node, AgentSpec)
            agent_node = self._build_agent_node(node.node, path)
            builder.add_node(path, agent_node.execute)
        builder.add_edge(path, END)

    def _build_orchestrator_node(
        self,
        node: GraphNode,
        parent_path: str | None,
    ) -> OrchestratorNode:
        assert isinstance(node.node, OrchestratorSpec)
        spec = node.node
        path = node_id(node, parent_path)
        model = self._build_model(spec.model)
        fallback = spec.model.fallback
        fallback_model = self._build_model(fallback) if fallback is not None else None

        children: list[ChildEntry] = []
        for child in node.children:
            child_node: AgentNode | OrchestratorNode
            if isinstance(child.node, AgentSpec):
                child_node = self._build_agent_node(child.node, node_id(child, path))
            else:
                child_node = self._build_orchestrator_node(child, path)
            children.append(
                ChildEntry(
                    id=child.node.id,
                    name=child.node.name or child.node.id,
                    protected=child.node.protected,
                    node=child_node,
                    description=child.node.description,
                )
            )

        return OrchestratorNode(
            spec=spec,
            node_path=path,
            model=model,
            children=children,
            filters=self._filters,
            resolver_loader=self._resolver_loader,
            base_dir=self._base_dir,
            usage_context=self._usage_context,
            usage_tracker=self._usage_tracker,
            fallback_model=fallback_model,
        )

    def _build_agent_node(self, spec: AgentSpec, node_path: str) -> AgentNode:
        tools, mcp_names, server_by_tool = self._build_agent_tools(spec)
        bound_model = self._build_model_runnable(spec.model, tools=tools)
        binding = AgentToolBinding(
            tools={tool.name: tool for tool in tools},
            mcp_tool_names=frozenset(mcp_names),
            mcp_server_by_tool=server_by_tool,
        )
        return AgentNode(
            spec=spec,
            node_path=node_path,
            bound_model=bound_model,
            resolver_loader=self._resolver_loader,
            base_dir=self._base_dir,
            tool_invoker=self._build_tool_invoker(spec, node_path, binding),
            usage_context=self._usage_context,
        )

    def _build_tool_invoker(
        self,
        spec: AgentSpec,
        node_path: str,
        binding: AgentToolBinding,
    ) -> ToolInvoker:
        return ToolInvoker(
            spec=spec,
            node_path=node_path,
            binding=binding,
            hook_manager=self._hook_manager,
            execution_manager=self._execution_manager,
            approval_coordinator=self._approval_coordinator,
            usage_tracker=self._usage_tracker,
            system_namespace=self._system_namespace,
        )

    def _build_model(self, model: BaseModelConfig) -> BaseChatModel:
        return build_model(self._model_factory, model)

    def _build_model_runnable(
        self,
        model: NodeModelConfig,
        tools: list[BaseTool] | None = None,
    ) -> BaseChatModel | Runnable:
        primary_model = self._build_model(model)
        bound_primary = primary_model.bind_tools(tools) if tools else primary_model
        if model.fallback is None:
            return bound_primary
        fallback_model = self._build_model(model.fallback)
        bound_fallback = fallback_model.bind_tools(tools) if tools else fallback_model
        return bound_primary.with_fallbacks(
            [bound_fallback],
            exceptions_to_handle=(Exception,),
        )

    def _build_agent_tools(
        self,
        spec: AgentSpec,
    ) -> tuple[list[BaseTool], set[str], dict[str, str]]:
        tools: list[BaseTool] = []
        mcp_names: set[str] = set()
        server_by_tool: dict[str, str] = {}
        for tool_spec in spec.tools:
            function = self._tool_loader.load(tool_spec.id)
            tools.append(StructuredTool.from_function(function, description=tool_spec.description))
        for mcp in spec.mcps:
            server_tools = self._mcp_tools.get(mcp.id, [])
            tools.extend(server_tools)
            for server_tool in server_tools:
                mcp_names.add(server_tool.name)
                server_by_tool[server_tool.name] = mcp.id
        return tools, mcp_names, server_by_tool
