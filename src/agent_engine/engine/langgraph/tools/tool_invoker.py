"""Coordination of a single tool invocation for one agent.

``ToolInvoker`` owns the path from "the model asked for this tool" to "here is
the text the model gets back": identity, execution limits, the Human-in-the-Loop
gate, idempotency, provider execution with lifecycle hooks, and handing the
outcome to the usage tracker. There is exactly one such path — the approval flow
and the tracking flow are steps in it, never a second pipeline.

Keeping it out of the node leaves ``AgentNode`` with prompt building and the
model loop, and lets the invoker be constructed and tested on its own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.invocation import ToolInvocation
from agent_engine.approvals.models import ToolExecutionStatus
from agent_engine.approvals.tool_execution_manager import (
    ToolExecutionClaim,
    ToolExecutionManager,
    execution_id_for,
)
from agent_engine.core.spec import AgentSpec
from agent_engine.engine.langgraph.tools.agent_tool_binding import AgentToolBinding
from agent_engine.engine.langgraph.tools.tool_gate import DenyTool, ExecuteTool, ToolGate
from agent_engine.engine.langgraph.tools.tool_result_normalizer import (
    ProviderToolResultError,
    ToolResultNormalizationError,
    normalize_tool_result,
)
from agent_engine.logging_config import log
from agent_engine.runtime.execution_limiter import (
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
from agent_engine.runtime.tool_models import ToolProviderName
from agent_engine.runtime.tool_results import NormalizedToolResult
from agent_engine.tool_usage.models import ToolCallIdentity, stable_tool_call_id
from agent_engine.tool_usage.tracker import ToolUsageTracker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ToolCall:
    """One resolved, about-to-run tool call: the tool plus its stable identity.

    Bundling these fields once removes the repeated ``agent/tool/provider/server``
    argument lists that the limit, gate, logging, hook, tracking, and execution
    steps would each otherwise rebuild from the raw ``tc`` dict.
    """

    tool: BaseTool
    identity: ToolCallIdentity
    args: dict[str, Any]
    exec_id: str

    @property
    def name(self) -> str:
        return self.identity.tool_name

    @property
    def provider(self) -> ToolProviderName:
        return self.identity.provider

    @property
    def server_id(self) -> str | None:
        return self.identity.server_id

    @property
    def tool_call_id(self) -> str:
        return self.identity.tool_call_id

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    @property
    def description(self) -> str:
        return self.tool.description


def _elapsed_ms(start: float) -> int:
    """Whole milliseconds elapsed since a ``time.perf_counter()`` reading."""
    return int((time.perf_counter() - start) * 1000)


class ToolInvoker:
    """Runs one agent's tool calls: gate, execute, record.

    All collaborators are injected, and none of them is per-run state, so one
    invoker serves every run of its agent concurrently.
    """

    def __init__(
        self,
        *,
        spec: AgentSpec,
        node_path: str,
        binding: AgentToolBinding,
        hook_manager: HookManager,
        execution_manager: ToolExecutionManager,
        approval_coordinator: ApprovalCoordinator,
        usage_tracker: ToolUsageTracker,
        system_namespace: str = "",
    ) -> None:
        self._spec = spec
        self._node_path = node_path
        self._binding = binding
        self._hook_manager = hook_manager
        self._execution_manager = execution_manager
        self._approval_coordinator = approval_coordinator
        self._usage = usage_tracker
        self._system_namespace = system_namespace

    async def invoke(self, tc: dict[str, Any]) -> NormalizedToolResult:
        """Resolve, gate, and execute one tool call for both local and MCP tools.

        The pipeline short-circuits at the first step that stops the call, each
        returning a normalized result whose text is model-facing:

        1. resolve the tool (unknown name → error);
        2. enforce execution limits (blocked → controlled message);
        3. the Human-in-the-Loop gate (denied → ``[denied]``; when approval is
           required and not yet granted the graph suspends and this method does
           not return normally);
        4. idempotency (a replayed call returns its recorded result);
        5. execute with the ``before/after/on_error`` lifecycle hooks.

        Every outcome that reached a decision — executed, failed, or denied — is
        recorded against the run before the result is returned.
        """
        tool = self._binding.get(tc["name"])
        if tool is None:
            return NormalizedToolResult.text_only(f"Unknown tool: {tc['name']}")
        call = self._describe_call(tool, tc)

        blocked = self._enforce_limits(call)
        if blocked is not None:
            return NormalizedToolResult.text_only(blocked)

        gate = await self._gate_tool_call(call)
        if isinstance(gate, DenyTool):
            await self._usage.record_denied(call.identity)
            return NormalizedToolResult.text_only(gate.message)

        return await self._execute(call)

    def _describe_call(self, tool: BaseTool, tc: dict[str, Any]) -> _ToolCall:
        """Freeze the tool's runtime identity: provider, ids, and idempotency key.

        ``tool_call_id`` is derived from the call itself rather than the
        provider's message id, so the same logical invocation keeps one identity
        across a suspension and its resumed replay. The conversation is recorded
        alongside it so a later turn can ask what earlier turns already did.
        """
        name: str = tc["name"]
        args = tc.get("args") or {}
        run_context = current_run_context.get()
        run_id = run_context.run_id if run_context and run_context.run_id else tc["id"]
        provider = self._binding.provider_of(name)
        server_id = self._binding.server_of(name)
        tool_call_id = stable_tool_call_id(run_id, self._node_path, provider, server_id, name, args)
        return _ToolCall(
            tool=tool,
            identity=ToolCallIdentity(
                run_id=run_id,
                agent_id=self._spec.id,
                agent_path=self._node_path,
                tool_call_id=tool_call_id,
                tool_name=name,
                provider=provider,
                server_id=server_id,
                conversation_id=run_context.conversation_id if run_context else None,
            ),
            args=args,
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

    async def _replay(self, call: _ToolCall, claim: ToolExecutionClaim) -> NormalizedToolResult:
        """Return the terminal result published by another execution owner."""
        if claim.result is None or claim.status is None:
            raise RuntimeError("replayed tool execution has no terminal result")
        log(
            logger,
            logging.INFO,
            "duplicate tool execution prevented",
            agent=self._spec.id,
            tool=call.name,
            tool_call_id=call.tool_call_id,
            execution_id=call.exec_id,
        )
        if claim.status == ToolExecutionStatus.SUCCEEDED:
            await self._usage.record_success(call.identity)
        else:
            await self._usage.record_failure(call.identity, error=claim.result.text)
        return claim.result

    async def _execute(self, call: _ToolCall) -> NormalizedToolResult:
        """Claim, run, normalize, and publish one logical tool invocation."""
        claim = await self._execution_manager.claim_execution(
            call.exec_id,
            tool_call_id=call.tool_call_id,
            run_id=call.run_id,
            tool_name=call.name,
        )
        if not claim.should_execute:
            return await self._replay(call, claim)

        self._log_call(logging.INFO, "tool call started", call)
        try:
            await self._hook_manager.run_before_tool_call(
                current_run_context.get(),
                ToolRequestContext(
                    agent_id=self._spec.id,
                    tool_name=call.name,
                    provider=call.provider,
                    server_id=call.server_id,
                ),
            )
        except BaseException:
            await self._publish_processing_failure(call, "Tool execution blocked before invocation")
            raise

        start = time.perf_counter()
        try:
            provider_result = await call.tool.ainvoke(
                {
                    "type": "tool_call",
                    "id": call.tool_call_id,
                    "name": call.name,
                    "args": call.args,
                }
            )
        except asyncio.CancelledError:
            await self._publish_processing_failure(call, "Tool execution was cancelled")
            raise
        except Exception as exc:
            return await self._record_error(call, exc, _elapsed_ms(start))
        except BaseException:
            await self._publish_processing_failure(call, "Tool execution was interrupted")
            raise

        try:
            normalized = normalize_tool_result(provider_result)
        except ProviderToolResultError as exc:
            return await self._record_error(
                call,
                exc,
                _elapsed_ms(start),
                model_text=exc.model_text,
            )
        except ToolResultNormalizationError as exc:
            return await self._record_error(
                call,
                exc,
                _elapsed_ms(start),
                model_text="Tool error: invalid tool result",
            )

        return await self._record_success(call, normalized, _elapsed_ms(start))

    async def _record_error(
        self,
        call: _ToolCall,
        exc: Exception,
        latency_ms: int,
        *,
        model_text: str | None = None,
    ) -> NormalizedToolResult:
        """Record a failed call, fire ``on_tool_error``, and return the error text.

        The failure is returned (not raised) so the model can read it and recover.
        """
        error = str(exc)[:200]
        result = NormalizedToolResult.text_only(model_text or f"Tool error: {exc}")
        await self._execution_manager.finish_execution(
            call.exec_id,
            status=ToolExecutionStatus.FAILED,
            result=result,
        )
        await self._usage.record_failure(call.identity, error=error)
        self._log_call(logging.WARNING, "tool call failed", call, ms=latency_ms, error=error)

        await self._hook_manager.run_on_tool_error(
            current_run_context.get(),
            self._call_context(call, "failed", latency_ms, error=error),
        )
        return result

    async def _record_success(
        self,
        call: _ToolCall,
        result: NormalizedToolResult,
        latency_ms: int,
    ) -> NormalizedToolResult:
        """Record a successful call, fire ``after_tool_call`` and result-transform
        hooks, persist the result to the idempotency ledger, and return it.
        """
        try:
            await self._usage.record_success(call.identity)
            self._log_call(logging.INFO, "tool call ended", call, ms=latency_ms)
            await self._hook_manager.run_after_tool_call(
                current_run_context.get(),
                self._call_context(call, "succeeded", latency_ms),
            )
            normalized = await self._transform_result(call, result, latency_ms)
        except BaseException:
            await self._publish_processing_failure(call, "Tool result processing failed")
            raise

        await self._execution_manager.finish_execution(
            call.exec_id,
            status=ToolExecutionStatus.SUCCEEDED,
            result=normalized,
        )
        return normalized

    async def _publish_processing_failure(self, call: _ToolCall, message: str) -> None:
        """Unblock duplicate callers without exposing exception payloads."""
        await self._execution_manager.finish_execution(
            call.exec_id,
            status=ToolExecutionStatus.FAILED,
            result=NormalizedToolResult.text_only(message),
        )

    async def _transform_result(
        self,
        call: _ToolCall,
        result: NormalizedToolResult,
        latency_ms: int,
    ) -> NormalizedToolResult:
        """Let ``transform_tool_result`` hooks reshape the result (e.g. truncate
        oversized MCP output). The context is only built when such a hook exists.
        """
        if not self._hook_manager.has("transform_tool_result"):
            return result
        transformed = await self._hook_manager.run_transform_tool_result(
            current_run_context.get(),
            ToolResultContext(
                agent_id=self._spec.id,
                tool_name=call.name,
                provider=call.provider,
                result=result.text,
                structured_result=result.structured,
                artifact=result.artifact,
                server_id=call.server_id,
                latency_ms=latency_ms,
            ),
        )
        return NormalizedToolResult(
            text=transformed.result,
            structured=transformed.structured_result,
            artifact=transformed.artifact,
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
            human_description=call.description,
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
