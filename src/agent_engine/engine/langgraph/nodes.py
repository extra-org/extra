"""LangGraph node callables.

``AgentNode``        — runs a single agent: resolve context → build prompt → tool loop.
``OrchestratorNode`` — supervisor agent that calls child agents as tools and synthesizes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.invocation import ToolInvocation
from agent_engine.approvals.manager import ToolExecutionManager, execution_id_for
from agent_engine.core.spec import AgentSpec, OrchestratorSpec
from agent_engine.engine.langgraph.filters import RouteFilter
from agent_engine.engine.langgraph.helpers import (
    as_text,
    emit_route,
    load_file,
    model_context,
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
from agent_engine.runtime.hooks.models import ToolStatus
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.streaming import current_streams
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


@dataclass(frozen=True)
class ExecuteTool:
    """Approval-gate result: the gate is open — the caller may run the tool."""


@dataclass(frozen=True)
class DenyTool:
    """Approval-gate result: the gate is closed — do not run the tool.

    ``message`` is the structured, model-facing result to return in place of
    execution (a normal denial, not a system error).
    """

    message: str


# A tagged result instead of ``str | None``: the caller branches on the type,
# never on a magic ``None`` that secretly means "proceed".
ToolGate = ExecuteTool | DenyTool


@dataclass(frozen=True)
class _ToolCall:
    """One resolved, about-to-run tool call: the tool plus its stable identity.

    Bundling these fields once removes the repeated ``agent/tool/provider/server``
    argument lists that the limit, gate, logging, hook, and execution steps would
    each otherwise rebuild from the raw ``tc`` dict.
    """

    tool: BaseTool
    name: str
    args: dict[str, Any]
    provider: ToolProviderName
    server_id: str | None
    tool_call_id: str
    run_id: str
    exec_id: str


def _elapsed_ms(start: float) -> int:
    """Whole milliseconds elapsed since a ``time.perf_counter()`` reading."""
    return int((time.perf_counter() - start) * 1000)


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
        execution_manager: ToolExecutionManager,
        approval_coordinator: ApprovalCoordinator,
        system_namespace: str = "",
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
        self._execution_manager = execution_manager
        self._approval_coordinator = approval_coordinator
        self._system_namespace = system_namespace

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
        messages = model_context(system_prompt, state.get("history", []), user_msg)
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
        """Resolve, gate, and execute one tool call for both local and MCP tools.

        The pipeline short-circuits at the first step that stops the call, each
        returning a model-facing string:

        1. resolve the tool (unknown name → error);
        2. enforce execution limits (blocked → controlled message);
        3. the Human-in-the-Loop gate (denied → ``[denied]``; when approval is
           required and not yet granted the graph suspends and this method does
           not return normally);
        4. idempotency (a replayed call returns its recorded result);
        5. execute with the ``before/after/on_error`` lifecycle hooks.
        """
        tool = self._tool_map.get(tc["name"])
        if tool is None:
            return f"Unknown tool: {tc['name']}"
        call = self._describe_call(tool, tc)

        blocked = self._enforce_limits(call)
        if blocked is not None:
            return blocked

        gate = await self._gate_tool_call(call)
        if isinstance(gate, DenyTool):
            return gate.message
        # gate is ExecuteTool — the gate is open; fall through to run the tool.

        cached = await self._cached_result(call, used_tools)
        if cached is not None:
            return cached

        return await self._execute(call, used_tools)

    def _describe_call(self, tool: BaseTool, tc: dict[str, Any]) -> _ToolCall:
        """Freeze the tool's runtime identity: provider, ids, and idempotency key."""
        name: str = tc["name"]
        args = tc.get("args") or {}
        run_context = current_run_context.get()
        run_id = run_context.run_id if run_context and run_context.run_id else tc["id"]
        provider: ToolProviderName = "mcp" if name in self._mcp_tool_names else "local"
        server_id = self._mcp_server_by_tool.get(name)
        stable_payload = json.dumps(
            [run_id, self._node_path, provider, server_id, name, args],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        tool_call_id = f"call_{hashlib.sha256(stable_payload.encode()).hexdigest()[:24]}"
        return _ToolCall(
            tool=tool,
            name=name,
            args=args,
            provider=provider,
            server_id=server_id,
            tool_call_id=tool_call_id,
            run_id=run_id,
            exec_id=execution_id_for(tool_call_id),
        )

    def _enforce_limits(self, call: _ToolCall) -> str | None:
        """Register the call against execution limits (total/per-agent counts,
        duplicate detection). Returns a controlled message when a limit blocks the
        call — which is then never executed — or ``None`` when it may proceed.
        """
        limiter = current_execution.get()
        if limiter is None:
            return None
        try:
            limiter.register_tool_call(self._spec.id, call.name, call.args)
        except ExecutionLimitExceeded as exc:
            log_limit(exc)
            return blocked_message(exc)
        return None

    async def _cached_result(
        self, call: _ToolCall, used_tools: list[ToolUsageRecord]
    ) -> str | None:
        """Return a prior successful result for this exact call, if any.

        Guards against a second side effect when a graph re-entry after resume
        replays the node with the same ``tool_call_id`` (the primary
        duplicate-execution protection).
        """
        cached = await self._execution_manager.already_executed(call.exec_id)
        if cached is None or cached.result is None:
            return None
        log(
            logger,
            logging.INFO,
            "duplicate tool execution prevented",
            agent=self._spec.id,
            tool=call.name,
            tool_call_id=call.tool_call_id,
            execution_id=call.exec_id,
        )
        used_tools.append(self._usage(call, "succeeded"))
        return cached.result

    async def _execute(self, call: _ToolCall, used_tools: list[ToolUsageRecord]) -> str:
        """Run the provider exactly once, wrapped in the idempotency ledger and the
        ``before_tool_call`` hook, dispatching to the success or error recorder.
        """
        await self._execution_manager.begin_execution(
            call.exec_id,
            tool_call_id=call.tool_call_id,
            run_id=call.run_id,
            tool_name=call.name,
        )
        self._log_call(logging.INFO, "tool call started", call)
        await self._hook_manager.run_before_tool_call(
            current_run_context.get(),
            ToolRequestContext(
                agent_id=self._spec.id,
                tool_name=call.name,
                provider=call.provider,
                server_id=call.server_id,
            ),
        )

        start = time.perf_counter()
        try:
            result = await call.tool.ainvoke(call.args)
        except Exception as exc:
            return await self._record_error(call, used_tools, exc, _elapsed_ms(start))
        return await self._record_success(call, used_tools, result, _elapsed_ms(start))

    async def _record_error(
        self,
        call: _ToolCall,
        used_tools: list[ToolUsageRecord],
        exc: Exception,
        latency_ms: int,
    ) -> str:
        """Record a failed call, fire ``on_tool_error``, and return the error text.

        The failure is returned (not raised) so the model can read it and recover.
        """
        error = str(exc)[:200]
        used_tools.append(self._usage(call, "failed", error=error))
        self._log_call(logging.WARNING, "tool call failed", call, ms=latency_ms)
        await self._hook_manager.run_on_tool_error(
            current_run_context.get(),
            self._call_context(call, "failed", latency_ms, error=error),
        )
        await self._execution_manager.finish_execution(
            call.exec_id, status="failed", result=f"Tool error: {exc}"
        )
        return f"Tool error: {exc}"

    async def _record_success(
        self,
        call: _ToolCall,
        used_tools: list[ToolUsageRecord],
        result: object,
        latency_ms: int,
    ) -> str:
        """Record a successful call, fire ``after_tool_call`` and result-transform
        hooks, persist the result to the idempotency ledger, and return it.
        """
        used_tools.append(self._usage(call, "succeeded"))
        self._log_call(logging.INFO, "tool call ended", call, ms=latency_ms)
        await self._hook_manager.run_after_tool_call(
            current_run_context.get(),
            self._call_context(call, "succeeded", latency_ms),
        )
        result_text = await self._transform_result(call, str(result), latency_ms)
        await self._execution_manager.finish_execution(
            call.exec_id, status="succeeded", result=result_text
        )
        return result_text

    async def _transform_result(self, call: _ToolCall, result_text: str, latency_ms: int) -> str:
        """Let ``transform_tool_result`` hooks reshape the result (e.g. truncate
        oversized MCP output). The context is only built when such a hook exists.
        """
        if not self._hook_manager.has("transform_tool_result"):
            return result_text
        transformed = await self._hook_manager.run_transform_tool_result(
            current_run_context.get(),
            ToolResultContext(
                agent_id=self._spec.id,
                tool_name=call.name,
                provider=call.provider,
                result=result_text,
                server_id=call.server_id,
                latency_ms=latency_ms,
            ),
        )
        return transformed.result

    def _usage(
        self, call: _ToolCall, status: ToolStatus, *, error: str | None = None
    ) -> ToolUsageRecord:
        """Build the per-call trace record with this node's identity filled in."""
        return ToolUsageRecord(
            name=call.name,
            provider=call.provider,
            status=status,
            agent_id=self._spec.id,
            server_id=call.server_id,
            error=error,
        )

    def _call_context(
        self,
        call: _ToolCall,
        status: ToolStatus,
        latency_ms: int,
        *,
        error: str | None = None,
    ) -> ToolCallContext:
        """Build the hook context for a completed call (success or failure)."""
        return ToolCallContext(
            agent_id=self._spec.id,
            tool_name=call.name,
            provider=call.provider,
            server_id=call.server_id,
            status=status,
            latency_ms=latency_ms,
            error=error,
        )

    def _log_call(self, level: int, event: str, call: _ToolCall, **fields: Any) -> None:
        """Structured log line carrying the call's identity (never its arguments)."""
        log(
            logger,
            level,
            event,
            agent=self._spec.id,
            tool=call.name,
            provider=call.provider,
            server=call.server_id,
            **fields,
        )

    async def _gate_tool_call(self, call: _ToolCall) -> ToolGate:
        """Run the call through the approval coordinator.

        Returns :class:`ExecuteTool` when the tool may run (auto mode, an existing
        session permission, or a human ``ALLOW_ONCE`` / ``ALLOW_FOR_SESSION``), or
        :class:`DenyTool` carrying a model-facing message when the user denied the
        call — a normal denial, not a system failure. When approval is required and
        not yet granted the coordinator suspends the run via the interrupt provider
        (``resolve`` does not return here), so no side effect happens before consent.
        """
        run_context = current_run_context.get()
        session_id = run_context.conversation_id if run_context else None
        auth_context = run_context.auth_context if run_context else None
        user_id = (
            auth_context.user_id
            if auth_context and auth_context.user_id is not None
            else run_context.user_id
            if run_context
            else None
        )
        organization_id = (
            auth_context.organization_id
            if auth_context and auth_context.organization_id is not None
            else run_context.organization_id
            if run_context
            else None
        )
        approval_id_value = run_context.metadata.get("approval_id") if run_context else None
        invocation = ToolInvocation(
            tool_call_id=call.tool_call_id,
            agent_id=self._spec.id,
            tool_name=call.name,
            session_id=session_id,
            provider=call.provider,
            server_id=call.server_id,
            arguments=call.args,
            system_namespace=self._system_namespace,
            user_id=user_id or "",
            organization_id=organization_id or "",
            run_id=run_context.run_id if run_context else None,
            approval_id=(approval_id_value if isinstance(approval_id_value, str) else None),
        )
        outcome = await self._approval_coordinator.resolve(
            invocation, auto_mode=self._spec.auto_mode
        )
        if outcome.execute:
            return ExecuteTool()
        self._log_call(logging.WARNING, "tool denied", call, tool_call_id=call.tool_call_id)
        return DenyTool(
            message=(
                f"[denied] status=denied toolCallId={call.tool_call_id} tool={call.name} "
                "reason=the user denied the action. The tool was not executed."
            )
        )


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
        model: BaseChatModel,
        children: list[ChildEntry],
        filters: list[RouteFilter],
        base_dir: Path,
        fallback_model: BaseChatModel | None = None,
    ) -> None:
        self._spec = spec
        self._node_path = node_path
        self._model = model
        self._fallback_model = fallback_model
        self._children = children
        self._filters = filters
        self._base_dir = base_dir

    async def __call__(self, state: GraphState) -> dict[str, object]:
        candidates = self._filter_children(state)
        system_content = load_file(self._base_dir, self._spec.prompts.system)
        orchestrator_content = load_file(self._base_dir, self._spec.prompts.orchestrator)
        if system_content or orchestrator_content:
            parts = [p for p in [system_content, orchestrator_content] if p]
            base_prompt = "\n\n".join(parts)
        else:
            base_prompt = self._spec.description
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
            # orchestrator's final synthesis does. The stream sinks are ambient
            # (current_streams); clear the answer sink for the child while keeping
            # route/token, so the live route still reflects the full chain.
            sub_state: dict[str, Any] = {
                "message": message,
                "visited": snapshot,
                "used_tools": [],
                "run_context": parent_state.get("run_context", {}),
            }
            child_sinks = replace(current_streams.get(), answer=None)
            sink_token = current_streams.set(child_sinks)
            try:
                result = await entry.callable(cast(GraphState, sub_state))
            except GraphInterrupt:
                # An approval interrupt raised inside a nested child must bubble up
                # to the LangGraph runtime so the checkpoint is taken — it is
                # control flow, not a child failure. Never swallow it.
                raise
            except Exception as exc:
                return f"Agent error: {exc}"
            finally:
                current_streams.reset(sink_token)

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
        if self._fallback_model is not None:
            bound_fallback = (
                self._fallback_model.bind_tools(tools) if tools else self._fallback_model
            )
            bound_model = bound_model.with_fallbacks(
                [bound_fallback], exceptions_to_handle=(Exception,)
            )
        tool_by_name = {t.name: t for t in tools}

        user_msg: str = state.get("message", "")
        messages = model_context(system_prompt, state.get("history", []), user_msg)
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
