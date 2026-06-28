from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
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
    render_graph,
    walk,
)
from agent_engine.engine.langgraph.nodes import AgentNode, ChildEntry, OrchestratorNode
from agent_engine.engine.types import RunResult
from agent_engine.loaders.import_roots import register_import_roots
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.logging_config import log
from agent_engine.models.factory import build_chat_model
from agent_engine.observability import build_callbacks
from agent_engine.runtime.hooks import (
    EngineContext,
    HookManager,
    RunContext,
    RunEndContext,
    current_run_context,
)
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.streaming import RunStreamEvent

logger = logging.getLogger(__name__)

ModelFactory = Callable[[str, str, float | None], BaseChatModel]


def _root_cause(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            return _root_cause(sub)
    return str(exc)


def _new_state(
    message: str,
    *,
    answer_stream: Callable[[str], None] | None = None,
    route_stream: Callable[[tuple[str, ...]], None] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {"message": message, "used_tools": []}
    if answer_stream is not None:
        state["answer_stream"] = answer_stream
    if route_stream is not None:
        state["route_stream"] = route_stream
    return state


def _trace_metadata(ctx: RunContext) -> dict[str, Any]:
    """Map per-run identity onto the metadata keys Langfuse reads to group traces.

    ``conversation_id`` becomes the Langfuse **session** id (a conversation is a
    session) and ``user_id`` the Langfuse user id. These keys are inert for any
    other callback, so this is a no-op when Langfuse tracing is disabled.
    """
    metadata: dict[str, Any] = {}
    if ctx.conversation_id:
        metadata["langfuse_session_id"] = ctx.conversation_id
    if ctx.user_id:
        metadata["langfuse_user_id"] = ctx.user_id
    return metadata


def _run_end_context(system_name: str, ctx: RunContext, result: RunResult) -> RunEndContext:
    """Safe summary of a completed run for on_run_end hooks (no answer text)."""
    return RunEndContext(
        run_id=ctx.run_id,
        system_name=system_name,
        status="succeeded",
        visited=tuple(result.visited),
        used_tool_count=len(result.used_tools),
    )


class LangGraphEngine(Engine):
    def __init__(
        self,
        base_dir: Path,
        *,
        model_factory: ModelFactory = build_chat_model,
        callbacks: list[BaseCallbackHandler] | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._model_factory = model_factory
        self._callbacks: list[BaseCallbackHandler] = [*build_callbacks(), *(callbacks or [])]

        self._app: CompiledStateGraph | None = None
        self._system_name = ""
        self._filters: list[RouteFilter] = []
        self._mcp_clients: dict[str, Any] = {}
        self._mcp_tools: dict[str, list[BaseTool]] = {}
        self._tool_loader: ToolLoader | None = None
        self._resolver_loader: ResolverLoader | None = None
        self._hook_manager: HookManager | None = None
        self._mcp_server_by_tool: dict[str, str] = {}

    async def build(self, spec: SystemSpec) -> None:
        self._system_name = spec.meta.name
        register_import_roots(self._base_dir, spec.plugins.import_roots)
        self._hook_manager = HookManager.from_config(
            spec.hooks,
            manifest_path=self._base_dir / "plugins" / "plugins.toml",
        )
        await self._hook_manager.run_engine_start(EngineContext(system_name=spec.meta.name))
        self._filters = self._setup_filters(spec)
        self._mcp_tools = await self._connect_mcps(spec)
        self._tool_loader = ToolLoader(self._base_dir)
        self._resolver_loader = ResolverLoader(self._base_dir)
        self._app = self._compile_graph(spec)
        self._log_startup_summary(spec)

    def _log_startup_summary(self, spec: SystemSpec) -> None:
        nodes = walk(spec.graph)
        agents = [n for n in nodes if isinstance(n.node, AgentSpec)]
        log(
            logger,
            logging.INFO,
            "system ready",
            system=self._system_name,
            agents=len(agents),
            orchestrators=len(nodes) - len(agents),
            tools=sum(len(n.node.tools) for n in nodes if isinstance(n.node, AgentSpec)),
            mcps=len(collect_mcp_specs(spec.graph)),
            resolvers=sum(len(n.node.resolvers) for n in nodes),
            protected_nodes=sum(1 for n in nodes if n.node.protected),
        )
        logger.info("graph:\n%s", "\n".join(render_graph(spec.graph)))

    async def close(self) -> None:
        # on_engine_stop runs best-effort before resources are released; a hook
        # failure is logged inside run_engine_stop and never blocks cleanup.
        if self._hook_manager is not None:
            log(logger, logging.INFO, "engine stopping", system=self._system_name)
            await self._hook_manager.run_engine_stop(EngineContext(system_name=self._system_name))
        self._mcp_clients.clear()
        self._mcp_tools.clear()

    async def _begin_run(self, context: RunContext | None) -> RunContext:
        hook_manager = self._require_built("running")[1]
        ctx = context or RunContext()
        if ctx.run_id is None:
            ctx = ctx.replace(run_id=str(uuid.uuid4()))
        return await hook_manager.run_run_start(ctx)

    def _require_built(self, action: str) -> tuple[CompiledStateGraph, HookManager]:
        if self._app is None or self._hook_manager is None:
            raise RuntimeError(f"Engine must be built before {action}")
        return self._app, self._hook_manager

    async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
        app, hook_manager = self._require_built("running")
        ctx = await self._begin_run(context)
        token = current_run_context.set(ctx)
        config = RunnableConfig(
            run_name=self._system_name,
            callbacks=self._callbacks,
            metadata=_trace_metadata(ctx),
        )
        log(logger, logging.INFO, "run started", run_id=ctx.run_id, system=self._system_name)
        try:
            result = await app.ainvoke(cast(Any, _new_state(message)), config)
            run_result = RunResult(
                system_name=self._system_name,
                visited=result.get("visited", []),
                answer=result.get("answer", ""),
                used_tools=tuple(result.get("used_tools", [])),
            )
            await hook_manager.run_run_end(
                ctx, _run_end_context(self._system_name, ctx, run_result)
            )
            log(
                logger,
                logging.INFO,
                "run ended",
                run_id=ctx.run_id,
                system=self._system_name,
                visited=len(run_result.visited),
                tools=len(run_result.used_tools),
            )
            return run_result
        except Exception as exc:
            log(
                logger,
                logging.WARNING,
                "run failed",
                run_id=ctx.run_id,
                system=self._system_name,
                error=type(exc).__name__,
            )
            await hook_manager.run_run_error(ctx, exc)
            raise
        finally:
            current_run_context.reset(token)

    async def stream(
        self, message: str, *, context: RunContext | None = None
    ) -> AsyncIterator[RunStreamEvent]:
        app, hook_manager = self._require_built("streaming")

        ctx = await self._begin_run(context)
        queue: asyncio.Queue[RunStreamEvent | BaseException | None] = asyncio.Queue()
        state = _new_state(
            message,
            answer_stream=lambda c: queue.put_nowait(
                RunStreamEvent(type="answer_delta", content=c)
            ),
            route_stream=lambda r: queue.put_nowait(RunStreamEvent(type="route", route=r)),
        )

        async def run_graph() -> None:
            config = RunnableConfig(
                run_name=self._system_name,
                callbacks=self._callbacks,
                metadata=_trace_metadata(ctx),
            )
            try:
                result = await app.ainvoke(cast(Any, state), config)
                visited = tuple(result.get("visited", []))
                used_tools = tuple(result.get("used_tools", []))
                queue.put_nowait(
                    RunStreamEvent(
                        type="final",
                        content=result.get("answer", ""),
                        route=visited,
                        system_name=self._system_name,
                        used_tools=used_tools,
                    )
                )
                await hook_manager.run_run_end(
                    ctx,
                    RunEndContext(
                        run_id=ctx.run_id,
                        system_name=self._system_name,
                        visited=visited,
                        used_tool_count=len(used_tools),
                    ),
                )
                log(
                    logger,
                    logging.INFO,
                    "run ended",
                    run_id=ctx.run_id,
                    system=self._system_name,
                    visited=len(visited),
                    tools=len(used_tools),
                )
            except Exception as exc:
                log(
                    logger,
                    logging.WARNING,
                    "run failed",
                    run_id=ctx.run_id,
                    system=self._system_name,
                    error=type(exc).__name__,
                )
                await hook_manager.run_run_error(ctx, exc)
                queue.put_nowait(exc)
            finally:
                queue.put_nowait(None)

        log(logger, logging.INFO, "run started", run_id=ctx.run_id, system=self._system_name)
        token = current_run_context.set(ctx)
        try:
            task = asyncio.create_task(run_graph())
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise RuntimeError(str(item)) from item
                yield item
            await task
        finally:
            current_run_context.reset(token)

    def _setup_filters(self, spec: SystemSpec) -> list[RouteFilter]:
        filters: list[RouteFilter] = []
        if has_protected_nodes(spec.graph):
            access_plugin = self._base_dir / "plugins" / "access.py"
            if access_plugin.is_file():
                filters.append(AccessFilter(self._base_dir))
        return filters

    async def _connect_mcps(self, spec: SystemSpec) -> dict[str, list[BaseTool]]:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        from agent_engine.loaders.mcp_auth_loader import MCPAuthLoader
        from agent_engine.loaders.mcp_tags import apply_tool_tags, effective_tool_tag_transport
        from agent_engine.runtime.hooks import HookedMCPAuth

        auth_loader = MCPAuthLoader(self._base_dir)
        assert self._hook_manager is not None
        hook_mcp_auth = self._hook_manager.has("before_mcp_request")

        mcp_tools: dict[str, list[BaseTool]] = {}
        for server_id, mcp_spec in collect_mcp_specs(spec.graph).items():
            config: dict[str, Any] = {"url": mcp_spec.url, "transport": "streamable_http"}
            auth = auth_loader.get_auth(server_id)
            if hook_mcp_auth:
                auth = HookedMCPAuth(self._hook_manager, server_id, base=auth)
            if auth is not None:
                config["auth"] = auth

            # Optional, per-server tool-discovery tags. No tags -> unchanged.
            # No explicit transport -> default header transport is applied.
            if mcp_spec.tool_tags:
                transport = effective_tool_tag_transport(mcp_spec)
                config = apply_tool_tags(config, mcp_spec.tool_tags, transport, server_id=server_id)
                log(
                    logger,
                    logging.INFO,
                    "mcp tool_tags configured",
                    server=server_id,
                    tags=len(mcp_spec.tool_tags),
                    transport=transport.type if transport else "",
                    default_transport=mcp_spec.tool_tag_transport is None,
                )

            client = MultiServerMCPClient({server_id: config})  # type: ignore[dict-item]
            self._mcp_clients[server_id] = client
            try:
                log(logger, logging.INFO, "mcp discovery started", server=server_id)
                mcp_tools[server_id] = await client.get_tools()
                log(
                    logger,
                    logging.INFO,
                    "mcp connected",
                    server=server_id,
                    tools=len(mcp_tools[server_id]),
                )
            except Exception as exc:
                log(
                    logger,
                    logging.WARNING,
                    "mcp unreachable",
                    server=server_id,
                    reason=_root_cause(exc),
                )
                mcp_tools[server_id] = []
        return mcp_tools

    def _compile_graph(self, spec: SystemSpec) -> CompiledStateGraph:
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
        assert self._hook_manager is not None
        tools, mcp_names, server_by_tool = self._build_agent_tools(spec)
        model = self._model_factory(spec.model.provider, spec.model.name, spec.model.temperature)
        bound_model = model.bind_tools(tools) if tools else model
        return AgentNode(
            spec=spec,
            node_path=node_path,
            bound_model=bound_model,
            tool_map={t.name: t for t in tools},
            mcp_tool_names=mcp_names,
            mcp_server_by_tool=server_by_tool,
            resolver_loader=self._resolver_loader,
            hook_manager=self._hook_manager,
            base_dir=self._base_dir,
        )

    def _build_agent_tools(
        self, spec: AgentSpec
    ) -> tuple[list[BaseTool], set[str], dict[str, str]]:
        assert self._tool_loader is not None
        tools: list[BaseTool] = []
        mcp_names: set[str] = set()
        server_by_tool: dict[str, str] = {}
        for t in spec.tools:
            fn = self._tool_loader.load(t.id)
            tools.append(StructuredTool.from_function(fn, description=t.description))
        for mcp in spec.mcps:
            server_tools = self._mcp_tools.get(mcp.id, [])
            tools.extend(server_tools)
            for st in server_tools:
                mcp_names.add(st.name)
                server_by_tool[st.name] = mcp.id
        return tools, mcp_names, server_by_tool
