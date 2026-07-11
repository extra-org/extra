"""LangGraph node callables.

``AgentNode``        — runs a single agent: resolve context → build prompt → tool loop.
``OrchestratorNode`` — supervisor agent that calls child agents as tools and synthesizes.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel

from agent_engine.approvals.contract import ToolContract, unknown_contract
from agent_engine.approvals.decision import ApprovalDecision, ToolCall
from agent_engine.approvals.manager import (
    ApprovalManager,
    ToolExecutionManager,
    execution_id_for,
)
from agent_engine.approvals.models import ApprovalDecisionKind, sanitize_arguments
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
        approval_manager: ApprovalManager,
        tool_contracts: dict[tuple[str | None, str], ToolContract],
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
        self._approval_manager = approval_manager
        self._tool_contracts = tool_contracts

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

        # Centralized Human-in-the-Loop gate. Every tool call — local or MCP —
        # passes through the same policy before the provider is invoked. DENY is
        # never executed; REQUIRE_APPROVAL suspends the graph (unless auto_mode).
        args: dict[str, Any] = tc.get("args") or {}
        gate = await self._gate_tool_call(name, tool, args, provider, server_id, tc)
        if gate is not None:
            return gate

        # Idempotency: a stable key derived from the tool_call_id. If this exact
        # call already executed (e.g. a graph re-entry after resume replays the
        # node), return the recorded result instead of causing a second side
        # effect. This is the primary duplicate-execution protection.
        run_id = run_context.run_id if run_context and run_context.run_id else tc["id"]
        exec_id = execution_id_for(tc["id"])
        cached = await self._execution_manager.already_executed(exec_id)
        if cached is not None and cached.result is not None:
            log(
                logger,
                logging.INFO,
                "duplicate tool execution prevented",
                agent=self._spec.id,
                tool=name,
                tool_call_id=tc["id"],
                execution_id=exec_id,
            )
            used_tools.append(
                ToolUsageRecord(
                    name=name,
                    provider=provider,
                    status="succeeded",
                    agent_id=self._spec.id,
                    server_id=server_id,
                )
            )
            return cached.result
        await self._execution_manager.begin_execution(
            exec_id, tool_call_id=tc["id"], run_id=run_id, tool_name=name
        )

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
            await self._execution_manager.finish_execution(
                exec_id, status="failed", result=f"Tool error: {exc}"
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
        await self._execution_manager.finish_execution(
            exec_id, status="succeeded", result=result_text
        )
        return result_text

    def _tool_schema(self, tool: BaseTool) -> dict[str, Any]:
        """Best-effort input-schema dict for the policy classifier.

        Uses the tool's declared argument schema (LangChain ``BaseTool.args``) so
        parameter names become an extra risk signal. Never raises — an
        unintrospectable tool simply contributes no schema tokens.
        """
        try:
            return {"properties": dict(tool.args)}
        except Exception:  # pragma: no cover - defensive; tool schema is optional
            return {}

    async def _gate_tool_call(
        self,
        name: str,
        tool: BaseTool,
        args: dict[str, Any],
        provider: ToolProviderName,
        server_id: str | None,
        tc: dict[str, Any],
    ) -> str | None:
        """Evaluate the approval policy and act on the decision.

        Returns ``None`` when the caller should execute the tool (EXECUTE, or an
        approved REQUIRE_APPROVAL). Returns a synthetic tool-result string when
        the call must not execute (DENY, or a rejected approval) — that string is
        fed back to the model as the tool's result.
        """
        call = ToolCall(
            tool_name=name,
            agent_id=self._spec.id,
            provider=provider,
            server_id=server_id,
            description=tool.description or "",
            input_schema=self._tool_schema(tool),
            arguments=args,
        )
        contract = self._tool_contracts.get((server_id if provider == "mcp" else None, name))
        if contract is None:
            contract = unknown_contract(
                "missing",
                reason="missing tool contract; failing closed",
                trusted=False,
            )
        verdict = self._execution_manager.decide(
            call, auto_mode=self._spec.auto_mode, contract=contract
        )
        if verdict.decision == ApprovalDecision.EXECUTE:
            return None
        if verdict.decision == ApprovalDecision.DENY:
            log(
                logger,
                logging.WARNING,
                "tool denied",
                agent=self._spec.id,
                tool=name,
                provider=provider,
                server=server_id,
                category=verdict.assessment.category.value,
            )
            return (
                f"[denied] The action '{name}' is not permitted by policy "
                f"({verdict.assessment.reason}). It was not executed."
            )
        decision = await self._require_approval(name, provider, server_id, verdict, args, tc)
        if decision == ApprovalDecisionKind.APPROVE:
            return None
        return (
            f"[rejected] status=rejected toolCallId={tc['id']} "
            "reason=user rejected the action. The tool was not executed."
        )

    async def _require_approval(
        self,
        name: str,
        provider: ToolProviderName,
        server_id: str | None,
        verdict: Any,
        args: dict[str, Any],
        tc: dict[str, Any],
    ) -> ApprovalDecisionKind:
        """Persist a pending approval, interrupt the graph, and return the human's
        decision once the run resumes.

        No tool side effect happens before the interrupt (prepare → interrupt →
        execute). On resume the node re-runs and ``interrupt()`` returns the
        decision instead of suspending again; ``create_pending`` is idempotent by
        ``tool_call_id`` so no duplicate approval is created.
        """
        run_context = current_run_context.get()
        run_id = run_context.run_id if run_context and run_context.run_id else tc["id"]
        authorized_user_id = run_context.user_id if run_context else None
        auth_ref = run_context.conversation_id if run_context else None
        approval_id = f"appr_{uuid.uuid4().hex[:16]}"

        record = await self._approval_manager.create_pending(
            run_id=run_id,
            thread_id=run_id,
            approval_id=approval_id,
            agent_id=self._spec.id,
            tool_name=name,
            tool_call_id=tc["id"],
            provider=provider,
            assessment=verdict.assessment,
            arguments=args,
            server_id=server_id,
            auth_ref=auth_ref,
            authorized_user_id=authorized_user_id,
        )

        payload = {
            "type": "tool_approval",
            "approval_id": record.approval_id,
            "run_id": run_id,
            "agent_id": self._spec.id,
            "tool_name": name,
            "provider": provider,
            "server_id": server_id,
            "category": verdict.assessment.category.value,
            "reason": verdict.assessment.reason,
            "arguments": sanitize_arguments(args),
        }
        log(
            logger,
            logging.INFO,
            "run interrupted",
            run_id=run_id,
            approval_id=record.approval_id,
            tool=name,
            tool_call_id=tc["id"],
        )
        resume = interrupt(payload)
        return _parse_decision(resume)


def _parse_decision(resume: Any) -> ApprovalDecisionKind:
    """Interpret a resume value as an approval decision (fail-safe to REJECT).

    Accepts an :class:`ApprovalDecisionKind`, a plain string ("approve"/"reject"),
    or a mapping with a ``decision`` key. Anything unrecognized is treated as a
    rejection so an ambiguous resume never triggers a side effect.
    """
    if isinstance(resume, ApprovalDecisionKind):
        return resume
    value: Any = resume
    if isinstance(resume, dict):
        value = resume.get("decision")
    if isinstance(value, str) and value.lower() in ("approve", "approved", "approve_and_edit"):
        return ApprovalDecisionKind.APPROVE
    return ApprovalDecisionKind.REJECT


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
