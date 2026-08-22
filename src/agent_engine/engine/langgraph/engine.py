from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agent_engine.approvals.approval_manager import ApprovalManager
from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.errors import RunNotFound
from agent_engine.approvals.in_memory_approval_repository import InMemoryApprovalRepository
from agent_engine.approvals.in_memory_session_approval_repository import (
    InMemorySessionApprovalRepository,
)
from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.models import ApprovalRecord, RunStatus
from agent_engine.approvals.session_approval_repository import SessionApprovalRepository
from agent_engine.approvals.session_approval_store import SessionApprovalStore
from agent_engine.approvals.tool_execution_manager import ToolExecutionManager
from agent_engine.core.execution import ExecutionPolicy
from agent_engine.core.spec import AgentSpec, BaseModelConfig, SystemSpec
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.approval_provider import InterruptApprovalProvider
from agent_engine.engine.langgraph.checkpointing import (
    CheckpointerHandle,
    CheckpointProviderFactory,
)
from agent_engine.engine.langgraph.execution.run_lifecycle import RunLifecycle
from agent_engine.engine.langgraph.execution.stream_channel import StreamChannel
from agent_engine.engine.langgraph.filters import AccessFilter, RouteFilter
from agent_engine.engine.langgraph.graph.graph_builder import (
    GraphBuilder,
    ModelFactory,
    RunGraph,
    model_factory_kwargs,
)
from agent_engine.engine.langgraph.graph.traversal import (
    collect_mcp_specs,
    has_protected_nodes,
    render_graph,
    walk,
)
from agent_engine.engine.langgraph.tools.mcp_connector import MCPConnector
from agent_engine.engine.types import ChatMessage, PendingApproval, RunResult
from agent_engine.loaders.import_roots import register_import_roots
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.logging_config import log
from agent_engine.models.factory import build_chat_model
from agent_engine.observability import build_callbacks
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runs.repository import RunRepository
from agent_engine.runtime.execution_limiter import ExecutionLimiter, current_execution
from agent_engine.runtime.hooks import (
    AuthContext,
    EngineContext,
    HookManager,
    RunContext,
    current_run_context,
)
from agent_engine.runtime.streaming import (
    RunStreamEvent,
    StreamSinks,
    TokenUsage,
    current_streams,
)
from agent_engine.runtime.tool_models import ToolUsageRecord
from agent_engine.tool_usage.context_provider import ToolUsageContextProvider
from agent_engine.tool_usage.in_memory import InMemoryToolUsageRepository
from agent_engine.tool_usage.repository import ToolUsageRepository
from agent_engine.tool_usage.trace import as_usage_records
from agent_engine.tool_usage.tracker import ToolUsageTracker

logger = logging.getLogger(__name__)


def _initial_state(
    message: str,
    *,
    history: Sequence[ChatMessage],
    ctx: RunContext,
    expose_run_context: bool,
) -> dict[str, Any]:
    """Build the graph's input state for one run.

    ``run_context`` is only put on the state when the caller supplied a context
    of their own, so a plain ``run("hi")`` leaves the key absent for filters.
    """
    state: dict[str, Any] = {
        "message": message,
        "history": [
            {"role": history_message.role.value, "content": history_message.content}
            for history_message in history
        ],
    }
    if expose_run_context:
        state["run_context"] = _state_run_context(ctx)
    return state


def _state_run_context(ctx: RunContext) -> dict[str, Any]:
    """Return the generic, non-secret context exposed to graph runtime filters."""
    data: dict[str, Any] = {}
    for key in ("run_id", "conversation_id", "user_id", "organization_id"):
        value = getattr(ctx, key)
        if value is not None:
            data[key] = value
    if ctx.metadata:
        data["metadata"] = dict(ctx.metadata)
    if ctx.auth_context is not None:
        auth: dict[str, Any] = {}
        for key in ("user_id", "organization_id"):
            value = getattr(ctx.auth_context, key)
            if value is not None:
                auth[key] = value
        if ctx.auth_context.scopes:
            auth["scopes"] = tuple(ctx.auth_context.scopes)
        if ctx.auth_context.roles:
            auth["roles"] = tuple(ctx.auth_context.roles)
        if ctx.auth_context.metadata:
            auth["metadata"] = dict(ctx.auth_context.metadata)
        if auth:
            data["auth"] = auth
    return data


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


def _pending_approval(approval: ApprovalRecord) -> PendingApproval:
    """Map a persisted approval record to the sanitized API/response shape."""
    return PendingApproval(
        run_id=approval.run_id,
        approval_id=approval.approval_id,
        agent_id=approval.agent_id,
        tool_name=approval.tool_name,
        description=approval.description,
        provider=approval.provider,
        server_id=approval.server_id,
        arguments=dict(approval.arguments),
    )


def _final_event(system_name: str, result: RunResult) -> RunStreamEvent:
    """The terminal event of a successful stream, mirroring its ``RunResult``."""
    return RunStreamEvent(
        type="final",
        content=result.answer,
        route=tuple(result.visited),
        system_name=system_name,
        used_tools=result.used_tools,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


def _pending_approval_event(system_name: str, result: RunResult) -> RunStreamEvent:
    """The terminal event of a stream suspended at an approval interrupt."""
    assert result.pending_approval is not None
    approval = result.pending_approval
    return RunStreamEvent(
        type="pending_approval",
        route=tuple(result.visited),
        system_name=system_name,
        used_tools=result.used_tools,
        run_id=approval.run_id,
        approval_id=approval.approval_id,
        agent_id=approval.agent_id,
        tool_name=approval.tool_name,
        description=approval.description,
        provider=approval.provider,
        server_id=approval.server_id,
        arguments=dict(approval.arguments),
    )


class LangGraphEngine(Engine):
    def __init__(
        self,
        base_dir: Path,
        *,
        model_factory: ModelFactory = build_chat_model,
        callbacks: list[BaseCallbackHandler] | None = None,
        checkpoint_connection_string: str | None = None,
        execution_manager: ToolExecutionManager | None = None,
        approval_manager: ApprovalManager | None = None,
        run_repository: RunRepository | None = None,
        session_approval_repository: SessionApprovalRepository | None = None,
        session_approval_store: SessionApprovalStore | None = None,
        tool_usage_repository: ToolUsageRepository | None = None,
    ) -> None:
        if session_approval_repository is not None and session_approval_store is not None:
            raise ValueError("pass session_approval_repository or session_approval_store, not both")
        self._base_dir = base_dir
        self._model_factory = model_factory
        self._callbacks: list[BaseCallbackHandler] = [*build_callbacks(), *(callbacks or [])]

        self._app: RunGraph | None = None
        self._defaults_model: BaseModelConfig | None = None
        self._system_name = ""
        self._filters: list[RouteFilter] = []
        self._mcp_connector: MCPConnector | None = None
        self._mcp_tools: dict[str, list[BaseTool]] = {}
        self._tool_loader: ToolLoader | None = None
        self._resolver_loader: ResolverLoader | None = None
        self._hook_manager: HookManager | None = None
        self._lifecycle: RunLifecycle | None = None
        self._policy = ExecutionPolicy()

        self._checkpoint_connection_string = checkpoint_connection_string
        self._checkpointer: CheckpointerHandle | None = None
        self._execution_manager = execution_manager or ToolExecutionManager(
            execution_repository=InMemoryToolExecutionRepository()
        )
        if approval_manager is not None and run_repository is not None:
            raise ValueError("pass approval_manager or run_repository, not both")
        if approval_manager is not None:
            self._run_repository = approval_manager.run_repository
            self._approval_manager = approval_manager
        else:
            self._run_repository = run_repository or InMemoryRunRepository()
            self._approval_manager = ApprovalManager(
                run_repository=self._run_repository,
                approval_repository=InMemoryApprovalRepository(),
            )
        self._session_approval_repository = (
            session_approval_repository or InMemorySessionApprovalRepository()
            if session_approval_store is None
            else None
        )
        self._approval_coordinator = ApprovalCoordinator(
            InterruptApprovalProvider(self._approval_manager),
            session_repository=self._session_approval_repository,
            session_store=session_approval_store,
        )
        # One usage repository per engine, so every agent of every run reads and
        # writes the same store; the writer and the reader are separate roles on
        # top of it.
        self._tool_usage_repository = tool_usage_repository or InMemoryToolUsageRepository()
        self._tool_usage_tracker = ToolUsageTracker(self._tool_usage_repository)
        self._tool_usage_context = ToolUsageContextProvider(self._tool_usage_repository)

    async def build(self, spec: SystemSpec) -> None:
        self._system_name = spec.meta.name
        self._defaults_model = spec.defaults.model if spec.defaults else None
        self._policy = spec.execution
        register_import_roots(self._base_dir, spec.plugins.import_roots)
        self._hook_manager = HookManager.from_config(
            spec.hooks,
            manifest_path=self._base_dir / "plugins" / "plugins.toml",
        )
        await self._hook_manager.run_engine_start(EngineContext(system_name=spec.meta.name))
        self._lifecycle = RunLifecycle(
            system_name=self._system_name,
            hook_manager=self._hook_manager,
            run_repository=self._run_repository,
        )
        self._filters = self._setup_filters(spec)
        self._mcp_connector = MCPConnector(self._base_dir, self._hook_manager)
        self._mcp_tools = await self._mcp_connector.connect(collect_mcp_specs(spec.graph))
        self._tool_loader = ToolLoader(self._base_dir)
        self._resolver_loader = ResolverLoader(self._base_dir)
        self._checkpointer = CheckpointProviderFactory().create(self._checkpoint_connection_string)
        self._app = self._build_graph(spec)
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
        if self._hook_manager is not None:
            log(logger, logging.INFO, "engine stopping", system=self._system_name)
            await self._hook_manager.run_engine_stop(EngineContext(system_name=self._system_name))
        if self._mcp_connector is not None:
            self._mcp_connector.clear()
        self._mcp_tools.clear()

    @property
    def can_complete_text(self) -> bool:
        return self._defaults_model is not None

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        trace_name: str | None = None,
    ) -> str:
        """One stateless model call, outside the compiled graph.

        Uses `defaults.model` verbatim (region, temperature, top_p included) —
        the same model a node with no `model:` of its own would get. Built
        fresh per call, not cached, so a per-call `max_tokens` reaches the
        provider through its own constructor kwarg rather than relying on
        every provider integration honoring an invoke-time override.
        """
        if self._defaults_model is None:
            raise RuntimeError(f"{self._system_name or 'this system'} has no default model")
        overrides: dict[str, object] | None = (
            {"max_tokens": max_tokens} if max_tokens is not None else None
        )
        model = self._model_factory(
            self._defaults_model.provider,
            self._defaults_model.name,
            self._defaults_model.temperature,
            **model_factory_kwargs(self._model_factory, self._defaults_model, overrides),
        )
        messages: list[BaseMessage] = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        config = RunnableConfig(callbacks=self._callbacks)
        if trace_name:
            config["run_name"] = trace_name
            config["tags"] = [trace_name]
        response = await model.ainvoke(messages, config=config)
        return response.text

    def discovered_mcp_tools(self) -> dict[str, tuple[str, ...]]:
        """Return discovered MCP tool names grouped by server for diagnostics/UIs."""
        return {
            server_id: tuple(sorted(tool.name for tool in tools))
            for server_id, tools in sorted(self._mcp_tools.items())
        }

    def _require_built(self, action: str) -> tuple[RunGraph, RunLifecycle]:
        """The two collaborators every run needs: the graph and its lifecycle.

        Both are created by ``build``; asking for them earlier is a programming
        error, so this fails loudly rather than executing a half-built engine.
        """
        if self._app is None or self._lifecycle is None:
            raise RuntimeError(f"Engine must be built before {action}")
        return self._app, self._lifecycle

    @contextmanager
    def _run_scope(self, ctx: RunContext, *, sinks: StreamSinks | None) -> Iterator[None]:
        """Publish the ambient per-run state for the duration of one run.

        Nodes, tools, and the MCP transport read the run context, the execution
        limiter, and the stream sinks from context vars instead of having them
        threaded through the graph state (which the checkpointer serializes).
        Anything started with ``asyncio.create_task`` *inside* this scope
        inherits them; anything started outside does not — which is why the
        streaming path creates its graph task here.

        ``sinks=None`` leaves ``current_streams`` untouched, for a leg of a run
        that must not disturb the sinks its caller already installed.
        """
        ctx_token = current_run_context.set(ctx)
        exec_token = current_execution.set(ExecutionLimiter(self._policy))
        stream_token = None if sinks is None else current_streams.set(sinks)
        try:
            yield
        finally:
            # An async generator may be finalized in a context other than the
            # consumer's. Tokens cannot be reset from that foreign context; in
            # that case there is no ambient value here to restore.
            if stream_token is not None:
                with suppress(ValueError):
                    current_streams.reset(stream_token)
            with suppress(ValueError):
                current_execution.reset(exec_token)
            with suppress(ValueError):
                current_run_context.reset(ctx_token)

    async def _invoke_graph(
        self, app: RunGraph, ctx: RunContext, graph_input: Any
    ) -> dict[str, Any]:
        """Execute the compiled graph and return its raw output.

        ``graph_input`` is the initial state of a new run, or the ``Command``
        that continues a suspended one from its checkpoint.
        """
        return await app.ainvoke(graph_input, self._thread_config(ctx))

    async def _completed_result(
        self,
        ctx: RunContext,
        result: dict[str, Any],
        *,
        token_usage: tuple[int | None, int | None] = (None, None),
    ) -> RunResult:
        """Map the graph's raw output onto the public run result."""
        input_tokens, output_tokens = token_usage
        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer=result.get("answer", ""),
            used_tools=await self._used_tools(ctx),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _used_tools(self, ctx: RunContext) -> tuple[ToolUsageRecord, ...]:
        """The run's tool trace, read from the usage repository.

        The trace is a projection of persisted usage — including tools called by
        nested agents — rather than something the graph state carried up.
        """
        if ctx.run_id is None:
            return ()
        return as_usage_records(await self._tool_usage_repository.list_for_run(ctx.run_id))

    async def _record_token_usage(
        self,
        ctx: RunContext,
        tokens: TokenUsage,
    ) -> tuple[int | None, int | None]:
        """Persist one execution leg and return cumulative usage for the run."""
        assert ctx.run_id is not None
        input_tokens, output_tokens = tokens.totals()
        run = await self._run_repository.add_token_usage(
            ctx.run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if run is None:
            raise RunNotFound(ctx.run_id)
        return run.input_tokens, run.output_tokens

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        app, lifecycle = self._require_built("running")
        ctx = await lifecycle.begin(context)
        state = _initial_state(
            message, history=history, ctx=ctx, expose_run_context=context is not None
        )
        tokens = TokenUsage()
        with self._run_scope(ctx, sinks=StreamSinks(token=tokens.add)):
            try:
                result = await self._invoke_graph(app, ctx, state)
                token_usage = await self._record_token_usage(ctx, tokens)
                pending = await self._pending_result(ctx, result, token_usage=token_usage)
                if pending is not None:
                    return pending
                run_result = await self._completed_result(ctx, result, token_usage=token_usage)
                await lifecycle.succeed(ctx, run_result)
                return run_result
            except Exception as exc:
                await lifecycle.fail(ctx, exc)
                raise

    def _thread_config(self, ctx: RunContext) -> RunnableConfig:
        """Build the run config, binding the LangGraph checkpoint thread_id.

        ``thread_id`` is the business ``run_id`` so a suspended run is resumed by
        the same identifier on any pod backed by a shared checkpointer.
        """
        return RunnableConfig(
            run_name=self._system_name,
            callbacks=self._callbacks,
            metadata=_trace_metadata(ctx),
            configurable={"thread_id": ctx.run_id},
        )

    async def _pending_result(
        self,
        ctx: RunContext,
        result: Any,
        *,
        token_usage: tuple[int | None, int | None] = (None, None),
    ) -> RunResult | None:
        """If the graph suspended at an approval interrupt, return a pending
        RunResult built from the persisted approval; otherwise return None."""
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if not interrupts:
            return None
        assert ctx.run_id is not None
        approval = await self._approval_manager.get_pending(ctx.run_id)
        if approval is None:
            return None
        log(
            logger,
            logging.INFO,
            "checkpoint persisted",
            run_id=ctx.run_id,
            thread_id=ctx.run_id,
            approval_id=approval.approval_id,
            backend=self._checkpointer.backend if self._checkpointer else "",
        )
        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer="",
            used_tools=await self._used_tools(ctx),
            input_tokens=token_usage[0],
            output_tokens=token_usage[1],
            status=RunStatus.PENDING_APPROVAL,
            pending_approval=_pending_approval(approval),
        )

    async def get_run_status(self, run_id: str) -> RunStatus:
        """Return the current status of a run (raises RunNotFound if unknown)."""
        run = await self._run_repository.get(run_id)
        if run is None:
            raise RunNotFound(run_id)
        return run.status

    async def get_pending_approval(self, run_id: str) -> PendingApproval | None:
        """Return the run's outstanding approval, or None if there is none."""
        approval = await self._approval_manager.get_pending(run_id)
        return _pending_approval(approval) if approval is not None else None

    async def get_processed_result(
        self,
        run_id: str,
        approval_id: str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
    ) -> RunResult | None:
        """Recover the result left by an already-processed approval decision."""
        app, _ = self._require_built("recovering a processed approval")
        await self._approval_manager.get_authorized(
            run_id=run_id,
            approval_id=approval_id,
            caller_user_id=caller_user_id,
            caller_auth_ref=caller_session_id,
        )
        run = await self._run_repository.get(run_id)
        if run is None:
            raise RunNotFound(run_id)
        if run.status not in {RunStatus.COMPLETED, RunStatus.PENDING_APPROVAL}:
            return None
        ctx = RunContext(run_id=run_id)
        snapshot = await app.aget_state(self._thread_config(ctx))
        values = snapshot.values
        if not isinstance(values, dict):
            return None
        token_usage = (run.input_tokens, run.output_tokens)
        if run.status == RunStatus.PENDING_APPROVAL:
            approval = await self.get_pending_approval(run_id)
            if approval is None:
                return None
            return RunResult(
                system_name=self._system_name,
                visited=values.get("visited", []),
                answer="",
                used_tools=await self._used_tools(ctx),
                input_tokens=token_usage[0],
                output_tokens=token_usage[1],
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=approval,
            )
        return await self._completed_result(ctx, values, token_usage=token_usage)

    async def resume(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
        access_token: str | None = None,
    ) -> RunResult:
        """Apply a human decision to a pending tool call and resume the same run.

        Atomically claims the approval (exactly one caller wins across pods),
        resumes the existing LangGraph thread from its checkpoint — so the agent
        is not re-selected and completed steps are not intentionally redone — and
        either executes or denies the original tool call. ``ALLOW_FOR_SESSION``
        additionally records a session permission so the tool is not re-prompted
        for the rest of the conversation.
        """
        app, lifecycle = self._require_built("resuming")
        kind = parse_decision(decision)
        ctx = await self._activate_approval_resume(
            lifecycle,
            run_id=run_id,
            approval_id=approval_id,
            caller_user_id=caller_user_id,
            caller_session_id=caller_session_id,
            access_token=access_token,
        )
        approved = kind != ApprovalDecision.DENY
        log(
            logger,
            logging.INFO,
            "resume started",
            run_id=run_id,
            approval_id=approval_id,
            decision=kind.value,
        )
        tokens = TokenUsage()
        with self._run_scope(ctx, sinks=StreamSinks(token=tokens.add)):
            try:
                resume_command: Command[Any] = Command(resume={"decision": kind.value})
                result = await self._invoke_graph(app, ctx, resume_command)
                token_usage = await self._record_token_usage(ctx, tokens)
                await self._approval_manager.finalize(approval_id, approved=approved)
                pending = await self._pending_result(ctx, result, token_usage=token_usage)
                if pending is not None:
                    return pending
                run_result = await self._completed_result(ctx, result, token_usage=token_usage)
                await lifecycle.succeed(ctx, run_result)
                log(
                    logger,
                    logging.INFO,
                    "run completed",
                    run_id=run_id,
                    approval_id=approval_id,
                    decision=kind.value,
                )
                return run_result
            except asyncio.CancelledError:
                await asyncio.shield(
                    lifecycle.cancel(ctx, reason="approval resume request cancelled")
                )
                raise
            except Exception as exc:
                await lifecycle.fail(ctx, exc)
                raise

    async def resume_stream(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
        access_token: str | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        """Resume one approval through the same owned stream used by new runs."""
        app, lifecycle = self._require_built("streaming an approval resume")
        kind = parse_decision(decision)
        ctx = await self._activate_approval_resume(
            lifecycle,
            run_id=run_id,
            approval_id=approval_id,
            caller_user_id=caller_user_id,
            caller_session_id=caller_session_id,
            access_token=access_token,
        )
        approved = kind != ApprovalDecision.DENY

        async def finalize_approval() -> None:
            await self._approval_manager.finalize(approval_id, approved=approved)

        log(
            logger,
            logging.INFO,
            "resume stream started",
            run_id=run_id,
            approval_id=approval_id,
            decision=kind.value,
        )
        command: Command[Any] = Command(resume={"decision": kind.value})
        execution = cast(
            AsyncGenerator[RunStreamEvent, None],
            self._stream_execution(
                app,
                lifecycle,
                ctx,
                command,
                after_invoke=finalize_approval,
                started_event=RunStreamEvent(type="resume_started", run_id=run_id),
            ),
        )
        try:
            async for event in execution:
                yield event
        finally:
            await execution.aclose()

    async def _activate_approval_resume(
        self,
        lifecycle: RunLifecycle,
        *,
        run_id: str,
        approval_id: str,
        caller_user_id: str | None,
        caller_session_id: str | None,
        access_token: str | None = None,
    ) -> RunContext:
        """Claim and activate one resume without an interruptible ownership gap.

        The response may be cancelled before its first SSE frame. Shielding this
        short transition ensures that cancellation observes either an unclaimed
        approval or an active run that it can terminally cancel, never a claimed
        approval stranded before graph-task ownership begins.
        """

        async def activate() -> RunContext:
            approval = await self._approval_manager.claim(
                run_id=run_id,
                approval_id=approval_id,
                caller_user_id=caller_user_id,
                caller_auth_ref=caller_session_id,
            )
            ctx = RunContext(
                run_id=run_id,
                conversation_id=approval.auth_ref,
                user_id=approval.authorized_user_id,
                organization_id=approval.organization_id,
                metadata={"approval_id": approval.approval_id},
                # The approver's credential as of now, not one captured when the
                # run started and possibly expired while it waited for a human.
                auth_context=AuthContext(
                    user_id=approval.authorized_user_id,
                    organization_id=approval.organization_id,
                    inbound_access_token=access_token,
                ),
            )
            await lifecycle.activate_resume(ctx)
            return ctx

        activation = asyncio.create_task(activate())
        try:
            return await asyncio.shield(activation)
        except asyncio.CancelledError:
            try:
                ctx = await asyncio.shield(activation)
            except Exception:
                # The claim lost or activation failed, so there is no active
                # execution owned by this request to terminally cancel.
                pass
            else:
                await asyncio.shield(
                    lifecycle.cancel(ctx, reason="approval resume request cancelled")
                )
            raise

    async def cancel_pending_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
    ) -> None:
        """Cancel a suspended HITL run if no approval decision has claimed it."""
        _, lifecycle = self._require_built("cancelling a pending approval")
        approval = await self._approval_manager.cancel_pending(
            run_id=run_id,
            approval_id=approval_id,
            caller_user_id=caller_user_id,
            caller_auth_ref=caller_session_id,
        )
        await lifecycle.cancel(
            RunContext(
                run_id=run_id,
                conversation_id=approval.auth_ref,
                user_id=approval.authorized_user_id,
                organization_id=approval.organization_id,
                metadata={"approval_id": approval.approval_id},
            ),
            reason="user cancelled pending approval",
        )

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        app, lifecycle = self._require_built("streaming")

        ctx = await lifecycle.begin(context)
        state = _initial_state(
            message, history=history, ctx=ctx, expose_run_context=context is not None
        )
        execution = cast(
            AsyncGenerator[RunStreamEvent, None],
            self._stream_execution(app, lifecycle, ctx, state),
        )
        try:
            async for event in execution:
                yield event
        finally:
            await execution.aclose()

    async def _stream_execution(
        self,
        app: RunGraph,
        lifecycle: RunLifecycle,
        ctx: RunContext,
        graph_input: Any,
        *,
        after_invoke: Callable[[], Awaitable[None]] | None = None,
        started_event: RunStreamEvent | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        """Own one active graph leg and cancel it when its consumer leaves."""
        channel = StreamChannel()
        tokens = TokenUsage()
        with self._run_scope(ctx, sinks=channel.sinks(token=tokens.add)):
            task = asyncio.create_task(
                self._stream_graph(
                    app,
                    lifecycle,
                    ctx,
                    graph_input,
                    channel=channel,
                    tokens=tokens,
                    after_invoke=after_invoke,
                )
            )
            if started_event is not None:
                channel.emit(started_event)
            try:
                async for event in channel.events():
                    yield event
                await task
            finally:
                await self._stop_abandoned_stream(task, lifecycle, ctx, tokens)

    async def _stop_abandoned_stream(
        self,
        task: asyncio.Task[None],
        lifecycle: RunLifecycle,
        ctx: RunContext,
        tokens: TokenUsage,
    ) -> None:
        """Stop a graph producer when its stream is closed before completion."""
        if task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.shield(self._cancel_abandoned_run(lifecycle, ctx, tokens))

    async def _cancel_abandoned_run(
        self,
        lifecycle: RunLifecycle,
        ctx: RunContext,
        tokens: TokenUsage,
    ) -> None:
        """Persist reported partial usage, then make cancellation terminal."""
        try:
            if tokens.totals() != (None, None):
                await self._record_token_usage(ctx, tokens)
        except Exception:
            logger.exception("failed to record token usage for cancelled run")
        finally:
            await lifecycle.cancel(ctx)

    async def _stream_graph(
        self,
        app: RunGraph,
        lifecycle: RunLifecycle,
        ctx: RunContext,
        graph_input: Any,
        *,
        channel: StreamChannel,
        tokens: TokenUsage,
        after_invoke: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Execute the graph for a streamed run and report the outcome.

        This is the channel's producer, running as its own task so the consumer
        can yield events while the graph is still executing. Every outcome —
        suspended, succeeded, failed — therefore has to leave through the
        channel: an exception raised here would reach nobody.
        """
        try:
            result = await self._invoke_graph(app, ctx, graph_input)
            token_usage = await self._record_token_usage(ctx, tokens)
            if after_invoke is not None:
                await after_invoke()
            pending = await self._pending_result(ctx, result, token_usage=token_usage)
            if pending is not None:
                channel.emit(_pending_approval_event(self._system_name, pending))
                return
            run_result = await self._completed_result(ctx, result, token_usage=token_usage)
            terminal = channel.publish_terminal(_final_event(self._system_name, run_result))
            await terminal.accepted.wait()
            try:
                await lifecycle.succeed(ctx, run_result)
            finally:
                terminal.finalized.set()
        except Exception as exc:
            await lifecycle.fail(ctx, exc)
            channel.abort(exc)
        finally:
            channel.close()

    def _setup_filters(self, spec: SystemSpec) -> list[RouteFilter]:
        filters: list[RouteFilter] = []
        if has_protected_nodes(spec.graph):
            access_plugin = self._base_dir / "plugins" / "access.py"
            if access_plugin.is_file():
                filters.append(AccessFilter(self._base_dir))
        return filters

    def _build_graph(self, spec: SystemSpec) -> RunGraph:
        """Delegate startup-only node assembly and compilation to ``GraphBuilder``."""
        assert self._tool_loader is not None
        assert self._resolver_loader is not None
        assert self._hook_manager is not None
        assert self._checkpointer is not None
        return GraphBuilder(
            base_dir=self._base_dir,
            model_factory=self._model_factory,
            filters=self._filters,
            mcp_tools=self._mcp_tools,
            tool_loader=self._tool_loader,
            resolver_loader=self._resolver_loader,
            hook_manager=self._hook_manager,
            checkpointer=self._checkpointer,
            execution_manager=self._execution_manager,
            approval_coordinator=self._approval_coordinator,
            usage_tracker=self._tool_usage_tracker,
            usage_context=self._tool_usage_context,
            system_namespace=self._system_name,
        ).compile(spec.graph)
