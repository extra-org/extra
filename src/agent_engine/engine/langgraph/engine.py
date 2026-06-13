from __future__ import annotations

import importlib.util
import logging
import re
import types
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from agent_engine.core.spec import AgentSpec, GraphNode, OrchestratorSpec, SystemSpec
from agent_engine.engine.engine import Engine
from agent_engine.engine.types import RunResult, ToolUsageRecord
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.models.factory import build_chat_model
from agent_engine.runtime.mcp_manager import MCPManager, MCPClientFactory
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.streaming import RunStreamEvent

logger = logging.getLogger(__name__)

ModelFactory = Callable[[str, str, float | None], BaseChatModel]


# ---------------------------------------------------------------------------
# RouteFilter — extensibility interface
# ---------------------------------------------------------------------------


class RouteFilter(ABC):
    """Filters routing candidates before the orchestrator LLM makes a decision.

    Implement this interface to add cross-cutting concerns (access control,
    feature flags, rate limiting, etc.) without touching the engine core.
    Filters run in order; each receives the list returned by the previous one.
    """

    @abstractmethod
    def filter(self, ctx: dict[str, Any], candidates: list[GraphNode]) -> list[GraphNode]: ...


class AccessFilter(RouteFilter):
    """Removes protected nodes the caller is not allowed to reach."""

    def __init__(self, base_dir: Path) -> None:
        self._resolver = _load_access_resolver(base_dir)

    def filter(self, ctx: dict[str, Any], candidates: list[GraphNode]) -> list[GraphNode]:
        return [n for n in candidates if self._resolver.can_access(ctx, n.node.id)]


# ---------------------------------------------------------------------------
# LangGraphEngine
# ---------------------------------------------------------------------------


class LangGraphEngine(Engine):
    def __init__(
        self,
        base_dir: Path,
        *,
        model_factory: ModelFactory = build_chat_model,
        mcp_client_factory: MCPClientFactory | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._model_factory = model_factory
        self._mcp_manager: MCPManager | None = None
        self._mcp_client_factory = mcp_client_factory
        self._app: CompiledStateGraph | None = None
        self._system_name = ""
        self._filters: list[RouteFilter] = []

    def build(self, spec: SystemSpec, filters: list[RouteFilter] | None = None) -> None:
        self._system_name = spec.meta.name
        self._filters = list(filters or [])

        if _has_protected_nodes(spec.graph):
            access_plugin = self._base_dir / "plugins" / "access.py"
            if access_plugin.is_file():
                self._filters.insert(0, AccessFilter(self._base_dir))

        mcp_configs = _collect_mcp_configs(spec.graph)
        self._mcp_manager = MCPManager(mcp_configs, client_factory=self._mcp_client_factory)

        tool_loader = ToolLoader(self._base_dir)
        resolver_loader = ResolverLoader(self._base_dir)

        builder = StateGraph(GraphState)
        self._add_nodes(builder, spec.graph, tool_loader, resolver_loader, parent_path=None)
        builder.add_edge(START, _node_path(spec.graph, parent_path=None))
        self._add_edges(builder, spec.graph, parent_path=None)
        self._app = builder.compile()

    def start(self) -> None:
        if self._mcp_manager is None:
            return
        from agent_engine.runtime.engine import _EngineAsyncLoop  # reuse existing loop helper

        self._loop = _EngineAsyncLoop()
        self._loop.run(self._mcp_manager.start())

    def stop(self) -> None:
        loop = getattr(self, "_loop", None)
        if loop is None:
            return
        self._loop = None
        try:
            if self._mcp_manager:
                loop.run(self._mcp_manager.stop())
        finally:
            loop.close()

    def run(self, message: str) -> RunResult:
        assert self._app is not None, "call build() before run()"
        used_tools: list[ToolUsageRecord] = []
        state: dict[str, Any] = {"message": message, "used_tools": used_tools}
        result = self._app.invoke(cast(Any, state))
        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer=result.get("answer", ""),
            used_tools=tuple(result.get("used_tools", used_tools)),
        )

    def stream(self, message: str) -> Iterator[RunStreamEvent]:
        assert self._app is not None, "call build() before stream()"
        import queue
        import threading

        event_queue: queue.Queue[RunStreamEvent | BaseException | None] = queue.Queue()
        used_tools: list[ToolUsageRecord] = []

        def run_graph() -> None:
            state: dict[str, Any] = {
                "message": message,
                "used_tools": used_tools,
                "answer_stream": lambda c: event_queue.put(RunStreamEvent(type="answer_delta", content=c)),
                "route_stream": lambda r: event_queue.put(RunStreamEvent(type="route", route=r)),
            }
            try:
                result = self._app.invoke(cast(Any, state))  # type: ignore[union-attr]
            except Exception as exc:
                event_queue.put(exc)
                return
            event_queue.put(RunStreamEvent(
                type="final",
                content=result.get("answer", ""),
                route=tuple(result.get("visited", [])),
                system_name=self._system_name,
                used_tools=tuple(result.get("used_tools", used_tools)),
            ))
            event_queue.put(None)

        worker = threading.Thread(target=run_graph, daemon=True)
        worker.start()
        try:
            while True:
                item = event_queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise RuntimeError(str(item)) from item
                yield item
        finally:
            worker.join()

    # ------------------------------------------------------------------
    # Graph building helpers
    # ------------------------------------------------------------------

    def _add_nodes(
        self,
        builder: StateGraph,
        node: GraphNode,
        tool_loader: ToolLoader,
        resolver_loader: ResolverLoader,
        parent_path: str | None,
    ) -> None:
        path = _node_path(node, parent_path)
        if isinstance(node.node, OrchestratorSpec):
            builder.add_node(path, _make_orchestrator_node(path))
        else:
            assert isinstance(node.node, AgentSpec)
            builder.add_node(
                path,
                self._make_agent_node(node.node, path, tool_loader, resolver_loader),
            )
        for child in node.children:
            self._add_nodes(builder, child, tool_loader, resolver_loader, parent_path=path)

    def _add_edges(
        self, builder: StateGraph, node: GraphNode, parent_path: str | None
    ) -> None:
        path = _node_path(node, parent_path)
        if node.children:
            routes = {_node_path(c, path): _node_path(c, path) for c in node.children}
            assert isinstance(node.node, OrchestratorSpec)
            builder.add_conditional_edges(
                path,
                self._make_router(node, path),
                routes,
            )
            for child in node.children:
                self._add_edges(builder, child, parent_path=path)
        else:
            builder.add_edge(path, END)

    def _make_router(
        self, node: GraphNode, node_path: str
    ) -> Callable[[GraphState], str]:
        assert isinstance(node.node, OrchestratorSpec)
        spec = node.node
        first_child_path = _node_path(node.children[0], node_path)
        children = list(node.children)

        routing_chain = None
        if spec.model.provider and spec.model.name:
            routing_chain = self._model_factory(
                spec.model.provider, spec.model.name, spec.model.temperature
            ).with_structured_output(_RouteDecision)

        def route(state: GraphState) -> str:
            ctx = state.get("run_context", {})
            candidates = list(children)
            for f in self._filters:
                candidates = f.filter(ctx, candidates)

            if not candidates:
                return first_child_path

            if routing_chain is not None:
                orchestrator_prompt = _load_file(self._base_dir, spec.prompts.orchestrator)
                child_desc = "\n".join(
                    f"- {c.node.id}: {c.node.description}" for c in candidates
                )
                system = (
                    f"{orchestrator_prompt}\n\nAvailable agents:\n{child_desc}\n\n"
                    "Respond with only the node_id of the best matching agent."
                )
                try:
                    decision = routing_chain.invoke(
                        [SystemMessage(content=system), HumanMessage(content=state.get("message", ""))]
                    )
                    if isinstance(decision, _RouteDecision):
                        for c in candidates:
                            if c.node.id == decision.next:
                                return _node_path(c, node_path)
                except Exception:
                    logger.warning("Orchestrator=%s LLM routing failed; using first candidate", node_path)

            return _node_path(candidates[0], node_path)

        return route

    def _make_agent_node(
        self,
        spec: AgentSpec,
        node_path: str,
        tool_loader: ToolLoader,
        resolver_loader: ResolverLoader,
    ) -> Callable[[GraphState], dict[str, object]]:
        tools = self._build_tools(spec, tool_loader)
        model = self._model_factory(spec.model.provider, spec.model.name, spec.model.temperature)
        bound_model = model.bind_tools(tools) if tools else model
        tool_map = {t.name: t for t in tools}

        def node(state: GraphState) -> dict[str, object]:
            ctx: dict[str, Any] = {}
            for r in spec.resolvers:
                fn = resolver_loader.load(spec.id, r.id)
                ctx[r.id] = str(fn(ctx))

            system_prompt = _render_prompt(
                _load_file(self._base_dir, spec.prompts.system) or spec.description,
                ctx,
            )
            messages: list[Any] = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=state.get("message", "")),
            ]
            route = (*state.get("visited", []), node_path)
            _stream_route(state, route)

            used_tools: list[ToolUsageRecord] = list(state.get("used_tools", []))
            response = cast(Any, _invoke_model(bound_model, messages, state))

            while getattr(response, "tool_calls", None):
                messages.append(response)
                for tc in response.tool_calls:
                    tool = tool_map[tc["name"]]
                    try:
                        result = tool.invoke(tc["args"])
                        used_tools.append(ToolUsageRecord(
                            name=tc["name"], provider="local",
                            status="succeeded", agent_id=spec.id,
                        ))
                    except Exception as exc:
                        used_tools.append(ToolUsageRecord(
                            name=tc["name"], provider="local",
                            status="failed", agent_id=spec.id, error=str(exc)[:200],
                        ))
                        raise
                    messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                response = cast(Any, _invoke_model(bound_model, messages, state))

            return {
                "visited": list(route),
                "answer": _as_text(response.content),
                "used_tools": used_tools,
            }

        return node

    def _build_tools(self, spec: AgentSpec, tool_loader: ToolLoader) -> list[BaseTool]:
        tools: list[BaseTool] = []
        for t in spec.tools:
            fn = tool_loader.load(t.id)
            tools.append(StructuredTool.from_function(fn, description=t.description))

        if self._mcp_manager and spec.mcps:
            for mcp in spec.mcps:
                for tool_def in self._mcp_manager.list_tools(mcp.id):
                    tools.append(_make_mcp_tool(tool_def, mcp.id, self._mcp_manager))

        return tools


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class _RouteDecision(BaseModel):
    next: str


def _node_path(node: GraphNode, parent_path: str | None) -> str:
    return f"{parent_path}/{node.node.id}" if parent_path else node.node.id


def _make_orchestrator_node(node_path: str) -> Callable[[GraphState], dict[str, object]]:
    def node(state: GraphState) -> dict[str, object]:
        return {"visited": [*state.get("visited", []), node_path]}
    return node


def _render_prompt(template: str, ctx: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:  # type: ignore[type-arg]
        return ctx.get(match.group(1).strip(), match.group(0))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def _load_file(base_dir: Path, rel_path: str | None) -> str:
    if not rel_path:
        return ""
    path = base_dir / rel_path
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _invoke_model(model: Any, messages: list, state: GraphState) -> Any:
    answer_stream = state.get("answer_stream")
    if not callable(answer_stream):
        return model.invoke(messages)
    streamed = None
    for chunk in model.stream(messages):
        streamed = chunk if streamed is None else streamed + chunk
        text = _as_text(getattr(chunk, "content", ""))
        if text:
            answer_stream(text)
    return streamed or AIMessage(content="")


def _stream_route(state: GraphState, route: tuple[str, ...]) -> None:
    fn = state.get("route_stream")
    if callable(fn):
        fn(route)


def _as_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return str(content)


def _has_protected_nodes(node: GraphNode) -> bool:
    if node.node.protected:
        return True
    return any(_has_protected_nodes(c) for c in node.children)


def _collect_mcp_configs(node: GraphNode) -> dict[str, Any]:
    configs: dict[str, Any] = {}
    if isinstance(node.node, AgentSpec):
        for mcp in node.node.mcps:
            configs[mcp.id] = type("McpSpec", (), {"url": mcp.url})()
    for child in node.children:
        configs.update(_collect_mcp_configs(child))
    return configs


def _load_access_resolver(base_dir: Path) -> Any:
    path = base_dir / "plugins" / "access.py"
    spec = importlib.util.spec_from_file_location("access", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    cls = getattr(module, "AccessResolver", None)
    if cls is None:
        raise ImportError(f"{path} must define class AccessResolver")
    return cls()


def _make_mcp_tool(tool_def: Any, server_id: str, mcp_manager: MCPManager) -> BaseTool:
    import asyncio
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def mcp_tool(**kwargs: Any) -> str:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            mcp_manager.call_tool(
                server_id=server_id,
                tool_name=tool_def.name,
                arguments=kwargs,
                ctx=type("Ctx", (), {"request_id": None, "trace_events": [], "add_trace_event": lambda self, e: None})(),
            )
        )
        return str(result)

    mcp_tool.name = tool_def.name
    mcp_tool.description = tool_def.description
    return mcp_tool
