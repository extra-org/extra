"""LangGraph node callables.

``AgentNode``        — runs a single agent: resolve context → build prompt → tool loop.
``OrchestratorNode`` — supervisor agent that calls child agents as tools and synthesizes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from agent_engine.core.spec import AgentSpec, OrchestratorSpec
from agent_engine.engine.langgraph.filters import RouteFilter
from agent_engine.engine.langgraph.helpers import (
    as_text,
    emit_route,
    load_file,
    render_prompt,
    run_tool_loop,
)
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.logging_config import log
from agent_engine.runtime.execution import (
    ExecutionLimitExceeded,
    blocked_message,
    current_execution,
    log_limit,
)
from agent_engine.runtime.hooks import (
    HookManager,
    ToolCallContext,
    ToolRequestContext,
    ToolResultContext,
    current_run_context,
)
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.tool_models import ToolProviderName, ToolUsageRecord

logger = logging.getLogger(__name__)

# Appended to every orchestrator system prompt.
# Enforces the core design contract: agents are the source of truth, not the LLM.
_ORCHESTRATOR_CONTRACT = """
## Instructions
- You MUST use the available agent tools to answer requests. Never answer from general knowledge.
- Only call a tool if its name/description clearly matches the request.
  Do NOT call a tool for something outside its stated scope.
- If no appropriate tool exists for part of the request, say: "I'm not able to help with that."
- You may call multiple tools if the request covers several topics.
"""


class _AgentCall(BaseModel):
    """Input schema for a child-agent tool."""

    message: str


@dataclass(frozen=True)
class ChildEntry:
    """A child node ready to be exposed as a tool by its parent orchestrator.

    Decoupled from the spec layer (``GraphNode``): the orchestrator only needs
    the child's identity, a human-readable description, its protection flag (for
    access filtering), and the runtime callable to invoke.
    """

    id: str
    name: str
    protected: bool
    callable: AgentNode | OrchestratorNode


class AgentNode:
    """Callable that implements one agent turn inside a LangGraph node.

    Dependencies are injected at construction time so the node is a plain
    callable with no hidden closure state.
    """

    def __init__(
        self,
        spec: AgentSpec,
        node_path: str,
        bound_model: Any,
        tool_map: dict[str, BaseTool],
        mcp_tool_names: set[str],
        resolver_loader: ResolverLoader,
        hook_manager: HookManager,
        base_dir: Path,
        mcp_server_by_tool: dict[str, str] | None = None,
    ) -> None:
        self._spec = spec
        self._node_path = node_path
        self._bound_model = bound_model
        self._tool_map = tool_map
        self._mcp_tool_names = mcp_tool_names
        self._mcp_server_by_tool = mcp_server_by_tool or {}
        self._resolver_loader = resolver_loader
        self._hook_manager = hook_manager
        self._base_dir = base_dir

    async def __call__(self, state: GraphState) -> dict[str, object]:
        ctx = self._resolve_context()
        system_prompt = self._build_prompt(ctx)
        return await self._run(system_prompt, state)

    def _resolve_context(self) -> dict[str, str]:
        """Run every declared resolver and return the accumulated key→value map.

        Resolvers are invoked in declaration order; each receives the values
        produced by previous resolvers so they can build on one another.
        """
        ctx: dict[str, str] = {}
        for r in self._spec.resolvers:
            ctx[r.id] = str(self._resolver_loader.load(self._spec.id, r.id)(ctx))
        return ctx

    def _build_prompt(self, ctx: dict[str, str]) -> str:
        """Load the system-prompt template and interpolate resolver values."""
        template = load_file(self._base_dir, self._spec.prompts.system) or self._spec.description
        return render_prompt(template, ctx)

    async def _run(self, system_prompt: str, state: GraphState) -> dict[str, object]:
        """Drive the model + tool loop until the model stops requesting tools."""
        user_msg: str = state.get("message", "")
        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        logger.debug("[%s] system:\n%s", self._node_path, system_prompt)
        logger.debug("[%s] → user: %s", self._node_path, user_msg)

        visited: list[str] = [*state.get("visited", []), self._node_path]
        emit_route(state, tuple(visited))

        used_tools: list[ToolUsageRecord] = list(state.get("used_tools", []))

        async def invoke_tool(tc: dict[str, Any]) -> str:
            return await self._invoke_tool(tc, used_tools)

        response = await run_tool_loop(
            self._bound_model, messages, state, self._node_path, invoke_tool
        )
        answer = as_text(response.content)
        logger.debug("[%s] ← response: %s", self._node_path, answer[:300])

        return {"visited": visited, "answer": answer, "used_tools": used_tools}

    async def _invoke_tool(self, tc: dict[str, Any], used_tools: list[ToolUsageRecord]) -> str:
        """Call one tool with full lifecycle hooks, for both local and MCP tools.

        Order: ``before_tool_call`` (observe-only/gate) → tool call →
        ``after_tool_call`` on success, ``on_tool_error`` on failure. Tool errors
        are returned as a string (not raised) so the model can read the failure
        and recover; the failure is still surfaced to ``on_tool_error`` hooks. A
        hook raising under the default fail-closed policy propagates and fails the
        run (use ``failure_policy: warn`` for best-effort audit hooks).
        """
        name: str = tc["name"]
        tool = self._tool_map.get(name)
        if tool is None:
            return f"Unknown tool: {name}"

        provider: ToolProviderName = "mcp" if name in self._mcp_tool_names else "local"
        server_id = self._mcp_server_by_tool.get(name)
        run_context = current_run_context.get()

        # Execution limits (total / per-agent tool calls, duplicate detection).
        # A blocked call is NOT executed; the model receives a controlled message.
        limiter = current_execution.get()
        if limiter is not None:
            try:
                limiter.register_tool_call(self._spec.id, name, tc.get("args"))
            except ExecutionLimitExceeded as exc:
                log_limit(exc)
                return blocked_message(exc)

        log(
            logger,
            logging.INFO,
            "tool call started",
            agent=self._spec.id,
            tool=name,
            provider=provider,
            server=server_id,
        )
        await self._hook_manager.run_before_tool_call(
            run_context,
            ToolRequestContext(
                agent_id=self._spec.id, tool_name=name, provider=provider, server_id=server_id
            ),
        )

        start = time.perf_counter()
        try:
            result = await tool.ainvoke(tc["args"])
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            error = str(exc)[:200]
            used_tools.append(
                ToolUsageRecord(
                    name=name,
                    provider=provider,
                    status="failed",
                    agent_id=self._spec.id,
                    server_id=server_id,
                    error=error,
                )
            )
            log(
                logger,
                logging.WARNING,
                "tool call failed",
                agent=self._spec.id,
                tool=name,
                provider=provider,
                server=server_id,
                ms=latency_ms,
            )
            await self._hook_manager.run_on_tool_error(
                run_context,
                ToolCallContext(
                    agent_id=self._spec.id,
                    tool_name=name,
                    provider=provider,
                    server_id=server_id,
                    status="failed",
                    latency_ms=latency_ms,
                    error=error,
                ),
            )
            return f"Tool error: {exc}"

        latency_ms = int((time.perf_counter() - start) * 1000)
        used_tools.append(
            ToolUsageRecord(
                name=name,
                provider=provider,
                status="succeeded",
                agent_id=self._spec.id,
                server_id=server_id,
            )
        )
        log(
            logger,
            logging.INFO,
            "tool call ended",
            agent=self._spec.id,
            tool=name,
            provider=provider,
            server=server_id,
            ms=latency_ms,
        )
        await self._hook_manager.run_after_tool_call(
            run_context,
            ToolCallContext(
                agent_id=self._spec.id,
                tool_name=name,
                provider=provider,
                server_id=server_id,
                status="succeeded",
                latency_ms=latency_ms,
            ),
        )

        result_text = str(result)
        # transform_tool_result hooks may reshape the result (e.g. truncate
        # oversized MCP output) before it is appended to the conversation. Gated
        # so ToolResultContext is only allocated when a hook exists.
        if self._hook_manager.has("transform_tool_result"):
            transformed = await self._hook_manager.run_transform_tool_result(
                run_context,
                ToolResultContext(
                    agent_id=self._spec.id,
                    tool_name=name,
                    provider=provider,
                    result=result_text,
                    server_id=server_id,
                    latency_ms=latency_ms,
                ),
            )
            result_text = transformed.result
        return result_text


class OrchestratorNode:
    """Supervisor-pattern orchestrator.

    Child agents (AgentNode or nested OrchestratorNode) are exposed as tools
    to the orchestrator LLM.  The LLM reads its system prompt, decides which
    tool(s) to call, collects their answers, and synthesises a final response.

    Access filters control which child tools are made available — if a child is
    filtered out the LLM simply does not have that tool and responds naturally
    (e.g. "I can't help with domestic flights").
    """

    def __init__(
        self,
        spec: OrchestratorSpec,
        node_path: str,
        model: Any,
        children: list[ChildEntry],
        filters: list[RouteFilter],
        base_dir: Path,
    ) -> None:
        self._spec = spec
        self._node_path = node_path
        self._model = model
        self._children = children
        self._filters = filters
        self._base_dir = base_dir

    async def __call__(self, state: GraphState) -> dict[str, object]:
        candidates = self._filter_children(state)
        base_prompt = load_file(self._base_dir, self._spec.prompts.system) or self._spec.description
        system_prompt = f"{base_prompt}\n{_ORCHESTRATOR_CONTRACT}"
        return await self._run(system_prompt, candidates, state)

    def _filter_children(self, state: GraphState) -> list[ChildEntry]:
        """Apply every RouteFilter to narrow down which child tools are available."""
        ctx: dict[str, Any] = state.get("run_context", {})
        candidates = list(self._children)
        for f in self._filters:
            candidates = f.filter(ctx, candidates)
        return candidates

    def _make_tool(
        self,
        entry: ChildEntry,
        parent_state: GraphState,
        visited_acc: list[str],
        used_tools_acc: list[ToolUsageRecord],
    ) -> StructuredTool:
        """Wrap a child node as a StructuredTool the orchestrator LLM can call.

        ``visited_acc`` and ``used_tools_acc`` are the parent's live trace lists.
        When the child returns we merge its new path segments and the tools it
        used back into them, so the final route and tool trace reflect the whole
        call-chain — not just the orchestrator's own step.
        """

        async def invoke(message: str) -> str:
            snapshot = list(visited_acc)
            # Children never stream their answer to the user — only the root
            # orchestrator's final synthesis does. `route_stream` is propagated so
            # the live route still reflects the full call-chain.
            sub_state: dict[str, Any] = {
                "message": message,
                "visited": snapshot,
                "used_tools": [],
                "route_stream": parent_state.get("route_stream"),
                "run_context": parent_state.get("run_context", {}),
            }
            try:
                result = await entry.callable(cast(GraphState, sub_state))
            except Exception as exc:
                return f"Agent error: {exc}"

            child_visited = cast("list[str]", result.get("visited", []))
            child_tools = cast("list[ToolUsageRecord]", result.get("used_tools", []))
            for path in child_visited[len(snapshot) :]:
                visited_acc.append(path)
            used_tools_acc.extend(child_tools)
            return cast(str, result.get("answer", ""))

        return StructuredTool.from_function(
            coroutine=invoke,
            name=entry.id,
            description=entry.name,
            args_schema=_AgentCall,
        )

    async def _run(
        self,
        system_prompt: str,
        candidates: list[ChildEntry],
        state: GraphState,
    ) -> dict[str, object]:
        """Drive the orchestrator LLM tool loop and return the synthesised answer."""
        visited: list[str] = [*state.get("visited", []), self._node_path]
        used_tools: list[ToolUsageRecord] = list(state.get("used_tools", []))

        # Build tools here so they share the live `visited` / `used_tools` lists.
        tools = [self._make_tool(e, state, visited, used_tools) for e in candidates]
        bound_model = self._model.bind_tools(tools) if tools else self._model
        tool_by_name = {t.name: t for t in tools}

        user_msg: str = state.get("message", "")
        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        logger.debug(
            "[%s] system:\n%s\ntools: %s", self._node_path, system_prompt, list(tool_by_name)
        )
        logger.debug("[%s] → user: %s", self._node_path, user_msg)

        emit_route(state, tuple(visited))

        async def invoke_tool(tc: dict[str, Any]) -> str:
            tool = tool_by_name.get(tc["name"])
            if tool is None:
                return f"Unknown agent: {tc['name']}"
            # Limit orchestrator→child-agent invocations. A blocked call returns a
            # controlled message instead of running the child.
            limiter = current_execution.get()
            if limiter is not None:
                try:
                    limiter.register_child_call(self._node_path, tc["name"])
                except ExecutionLimitExceeded as exc:
                    log_limit(exc)
                    return blocked_message(exc)
            return cast(str, await tool.ainvoke(tc["args"]))

        response = await run_tool_loop(bound_model, messages, state, self._node_path, invoke_tool)
        answer = as_text(response.content)
        logger.debug("[%s] ← response: %s", self._node_path, answer[:300])

        return {"visited": visited, "answer": answer, "used_tools": used_tools}
