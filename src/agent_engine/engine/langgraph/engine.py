from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_engine.core.spec import AgentSpec, GraphNode, OrchestratorSpec, SystemSpec
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.filters import AccessFilter, RouteFilter
from agent_engine.engine.langgraph.helpers import (
    collect_mcp_specs,
    has_protected_nodes,
    node_id,
)
from agent_engine.engine.langgraph.nodes import AgentNode, ChildEntry, OrchestratorNode
from agent_engine.engine.types import RunResult
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.models.factory import build_chat_model
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.streaming import RunStreamEvent

logger = logging.getLogger(__name__)

ModelFactory = Callable[[str, str, float | None], BaseChatModel]


def _root_cause(exc: BaseException) -> str:
    """Return the message of the deepest non-group exception."""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            return _root_cause(sub)
    return str(exc)


class LangGraphEngine(Engine):
    def __init__(
        self,
        base_dir: Path,
        *,
        model_factory: ModelFactory = build_chat_model,
    ) -> None:
        self._base_dir = base_dir
        self._model_factory = model_factory

        # set during build()
        self._app: CompiledStateGraph | None = None
        self._system_name = ""
        self._filters: list[RouteFilter] = []
        self._mcp_clients: dict[str, Any] = {}
        self._mcp_tools: dict[str, list[BaseTool]] = {}
        self._tool_loader: ToolLoader | None = None
        self._resolver_loader: ResolverLoader | None = None

    async def build(self, spec: SystemSpec) -> None:
        self._system_name = spec.meta.name
        self._filters = self._setup_filters(spec)
        self._mcp_tools = await self._connect_mcps(spec)
        self._tool_loader = ToolLoader(self._base_dir)
        self._resolver_loader = ResolverLoader(self._base_dir)
        self._app = self._compile_graph(spec)

    async def close(self) -> None:
        self._mcp_clients.clear()
        self._mcp_tools.clear()

    def _new_state(
        self,
        message: str,
        *,
        answer_stream: Callable[[str], None] | None = None,
        route_stream: Callable[[tuple[str, ...]], None] | None = None,
    ) -> dict[str, Any]:
        """Build the initial per-request state. Stream callbacks are optional."""
        state: dict[str, Any] = {"message": message, "used_tools": []}
        if answer_stream is not None:
            state["answer_stream"] = answer_stream
        if route_stream is not None:
            state["route_stream"] = route_stream
        return state

    async def run(self, message: str) -> RunResult:
        assert self._app is not None, "call build() before run()"
        result = await self._app.ainvoke(cast(Any, self._new_state(message)))
        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer=result.get("answer", ""),
            used_tools=tuple(result.get("used_tools", [])),
        )

    async def stream(self, message: str) -> AsyncIterator[RunStreamEvent]:
        assert self._app is not None, "call build() before stream()"

        queue: asyncio.Queue[RunStreamEvent | BaseException | None] = asyncio.Queue()
        state = self._new_state(
            message,
            answer_stream=lambda c: queue.put_nowait(
                RunStreamEvent(type="answer_delta", content=c)
            ),
            route_stream=lambda r: queue.put_nowait(RunStreamEvent(type="route", route=r)),
        )

        async def run_graph() -> None:
            try:
                result = await self._app.ainvoke(cast(Any, state))  # type: ignore[union-attr]
                queue.put_nowait(RunStreamEvent(
                    type="final",
                    content=result.get("answer", ""),
                    route=tuple(result.get("visited", [])),
                    system_name=self._system_name,
                    used_tools=tuple(result.get("used_tools", [])),
                ))
            except Exception as exc:
                queue.put_nowait(exc)
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run_graph())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise RuntimeError(str(item)) from item
                yield item
        finally:
            await task

    def _setup_filters(self, spec: SystemSpec) -> list[RouteFilter]:
        filters: list[RouteFilter] = []
        if has_protected_nodes(spec.graph):
            access_plugin = self._base_dir / "plugins" / "access.py"
            if access_plugin.is_file():
                filters.append(AccessFilter(self._base_dir))
        return filters

    async def _connect_mcps(self, spec: SystemSpec) -> dict[str, list[BaseTool]]:
        """Create one MultiServerMCPClient per server and fetch its tools.

        If plugins/mcp_auth/{server_id}.py exists, its get_headers() is wired in
        as per-request auth, so rotating credentials are resolved fresh on every
        call rather than frozen at startup. Unreachable servers are logged as
        warnings and skipped.
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient

        from agent_engine.loaders.mcp_auth_loader import MCPAuthLoader

        auth_loader = MCPAuthLoader(self._base_dir)

        mcp_tools: dict[str, list[BaseTool]] = {}
        for server_id, mcp_spec in collect_mcp_specs(spec.graph).items():
            config: dict[str, Any] = {"url": mcp_spec.url, "transport": "streamable_http"}
            auth = auth_loader.get_auth(server_id)
            if auth is not None:
                config["auth"] = auth

            client = MultiServerMCPClient({server_id: config})  # type: ignore[dict-item]
            self._mcp_clients[server_id] = client
            try:
                mcp_tools[server_id] = await client.get_tools()
                logger.info("MCP server=%s tools=%d", server_id, len(mcp_tools[server_id]))
            except Exception as exc:
                reason = _root_cause(exc)
                logger.warning(
                    "MCP server=%s unreachable, skipping its tools: %s", server_id, reason
                )
                mcp_tools[server_id] = []
        return mcp_tools

    def _compile_graph(self, spec: SystemSpec) -> CompiledStateGraph:
        """Walk the spec tree and wire nodes + edges into a StateGraph."""
        builder = StateGraph(GraphState)
        self._wire_node(builder, spec.graph, parent_path=None)
        builder.add_edge(START, node_id(spec.graph, parent_path=None))
        return builder.compile()

    def _wire_node(
        self,
        builder: StateGraph,
        node: GraphNode,
        parent_path: str | None,
    ) -> None:
        """Add one node to the graph.

        Orchestrators embed their children as tools — no conditional edges needed.
        The graph is therefore a flat list of root-level nodes each connected to END.
        """
        path = node_id(node, parent_path)

        if isinstance(node.node, OrchestratorSpec):
            builder.add_node(path, self._build_orchestrator_node(node, parent_path))
        else:
            assert isinstance(node.node, AgentSpec)
            builder.add_node(path, self._build_agent_node(node.node, path))

        builder.add_edge(path, END)

    def _build_orchestrator_node(
        self,
        node: GraphNode,
        parent_path: str | None,
    ) -> OrchestratorNode:
        """Build an OrchestratorNode whose children become tools (recursive)."""
        assert isinstance(node.node, OrchestratorSpec)
        spec = node.node
        path = node_id(node, parent_path)
        model = self._model_factory(spec.model.provider, spec.model.name, spec.model.temperature)

        children: list[ChildEntry] = []
        for child in node.children:
            callable_node: AgentNode | OrchestratorNode
            if isinstance(child.node, AgentSpec):
                callable_node = self._build_agent_node(child.node, node_id(child, path))
            else:
                callable_node = self._build_orchestrator_node(child, path)
            children.append(
                ChildEntry(
                    id=child.node.id,
                    name=child.node.name or child.node.id,
                    protected=child.node.protected,
                    callable=callable_node,
                )
            )

        return OrchestratorNode(
            spec=spec,
            node_path=path,
            model=model,
            children=children,
            filters=self._filters,
            base_dir=self._base_dir,
        )

    def _build_agent_node(self, spec: AgentSpec, node_path: str) -> AgentNode:
        assert self._tool_loader is not None
        assert self._resolver_loader is not None
        tools, mcp_names = self._build_agent_tools(spec)
        model = self._model_factory(spec.model.provider, spec.model.name, spec.model.temperature)
        bound_model = model.bind_tools(tools) if tools else model
        return AgentNode(
            spec=spec,
            node_path=node_path,
            bound_model=bound_model,
            tool_map={t.name: t for t in tools},
            mcp_tool_names=mcp_names,
            resolver_loader=self._resolver_loader,
            base_dir=self._base_dir,
        )

    def _build_agent_tools(self, spec: AgentSpec) -> tuple[list[BaseTool], set[str]]:
        """Return all tools for an agent and the set of MCP-sourced tool names."""
        assert self._tool_loader is not None
        tools: list[BaseTool] = []
        mcp_names: set[str] = set()
        for t in spec.tools:
            fn = self._tool_loader.load(t.id)
            tools.append(StructuredTool.from_function(fn, description=t.description))
        for mcp in spec.mcps:
            server_tools = self._mcp_tools.get(mcp.id, [])
            tools.extend(server_tools)
            mcp_names.update(t.name for t in server_tools)
        return tools, mcp_names
