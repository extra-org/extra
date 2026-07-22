from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.manager import ApprovalManager, ToolExecutionManager
from agent_engine.approvals.models import ApprovalRecord, RunRecord, RunStatus
from agent_engine.approvals.repository import (
    InMemoryApprovalRepository,
    InMemoryRunRepository,
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.session_store import (
    InMemorySessionApprovalRepository,
    SessionApprovalRepository,
    SessionApprovalStore,
)
from agent_engine.core.execution import ExecutionPolicy
from agent_engine.core.spec import AgentSpec, GraphNode, OrchestratorSpec, SystemSpec
from agent_engine.core.spec import ModelConfig as NodeModelConfig
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.approval_provider import InterruptApprovalProvider
from agent_engine.engine.langgraph.checkpointing import (
    CheckpointerHandle,
    CheckpointProviderFactory,
)
from agent_engine.engine.langgraph.filters import AccessFilter, RouteFilter
from agent_engine.engine.langgraph.helpers import (
    collect_mcp_specs,
    has_protected_nodes,
    node_id,
    render_graph,
    walk,
)
from agent_engine.engine.langgraph.nodes import AgentNode, ChildEntry, OrchestratorNode
from agent_engine.engine.types import ChatMessage, PendingApproval, RunResult
from agent_engine.loaders.import_roots import register_import_roots
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.logging_config import log
from agent_engine.models.factory import build_chat_model
from agent_engine.observability import build_callbacks
from agent_engine.runtime.execution import ExecutionLimiter, current_execution
from agent_engine.runtime.hooks import (
    EngineContext,
    HookManager,
    RunContext,
    RunEndContext,
    current_run_context,
)
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.streaming import RunStreamEvent, StreamSinks, current_streams

logger = logging.getLogger(__name__)

ModelFactory = Callable[..., BaseChatModel]
_MODEL_FACTORY_OPTIONAL_KWARGS = ("region", "max_tokens", "top_p")


def _root_cause(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            return _root_cause(sub)
    return str(exc)


def _new_state(
    message: str,
    *,
    history: Sequence[ChatMessage] = (),
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "message": message,
        "history": [
            {"role": history_message.role.value, "content": history_message.content}
            for history_message in history
        ],
        "used_tools": [],
    }
    if run_context is not None:
        state["run_context"] = run_context
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


def _run_end_context(system_name: str, ctx: RunContext, result: RunResult) -> RunEndContext:
    """Safe summary of a completed run for on_run_end hooks (no answer text)."""
    return RunEndContext(
        run_id=ctx.run_id,
        system_name=system_name,
        status="succeeded",
        visited=tuple(result.visited),
        used_tool_count=len(result.used_tools),
    )


def _model_factory_kwargs(factory: ModelFactory, model: NodeModelConfig) -> dict[str, object]:
    optional = {
        "region": model.region,
        "max_tokens": model.max_tokens,
        "top_p": model.top_p,
        "cache_system_prompt": model.cache_system_prompt,
    }
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
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters):
        return present
    accepted = set(signature.parameters)
    return {key: value for key, value in present.items() if key in accepted}


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
        session_approval_repository: SessionApprovalRepository | None = None,
        session_approval_store: SessionApprovalStore | None = None,
    ) -> None:
        if session_approval_repository is not None and session_approval_store is not None:
            raise ValueError("pass session_approval_repository or session_approval_store, not both")
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
        self._policy = ExecutionPolicy()

        # Human-in-the-Loop wiring. Checkpointer, approval/execution managers, and
        # the deterministic approval coordinator are selected once here (Dependency
        # Inversion) and injected into every node, so both local and MCP tools
        # share one centralized approval path and the graph/resume logic is
        # identical for in-memory and persistent backends. There is no LLM-based
        # risk classification: every tool requires approval unless the agent is in
        # auto mode or the tool was already approved for the session.
        self._checkpoint_connection_string = checkpoint_connection_string
        self._checkpointer: CheckpointerHandle | None = None
        self._execution_manager = execution_manager or ToolExecutionManager(
            execution_repository=InMemoryToolExecutionRepository()
        )
        self._approval_manager = approval_manager or ApprovalManager(
            run_repository=InMemoryRunRepository(),
            approval_repository=InMemoryApprovalRepository(),
        )
        # Composition roots inject a shared or persistent repository. The fallback
        # keeps direct engine construction backwards-compatible for tests and
        # embedded local use.
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

    async def build(self, spec: SystemSpec) -> None:
        self._system_name = spec.meta.name
        self._policy = spec.execution
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
        self._checkpointer = CheckpointProviderFactory().create(self._checkpoint_connection_string)
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

    def discovered_mcp_tools(self) -> dict[str, tuple[str, ...]]:
        """Return discovered MCP tool names grouped by server for diagnostics/UIs."""
        return {
            server_id: tuple(sorted(tool.name for tool in tools))
            for server_id, tools in sorted(self._mcp_tools.items())
        }

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

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        app, hook_manager = self._require_built("running")
        has_caller_context = context is not None
        ctx = await self._begin_run(context)
        token = current_run_context.set(ctx)
        exec_token = current_execution.set(ExecutionLimiter(self._policy))
        input_tokens = 0
        output_tokens = 0

        def accumulate_tokens(inp: int, out: int) -> None:
            nonlocal input_tokens, output_tokens
            input_tokens += inp
            output_tokens += out

        stream_token = current_streams.set(StreamSinks(token=accumulate_tokens))
        config = self._thread_config(ctx)
        await self._register_run(ctx)
        log(logger, logging.INFO, "run started", run_id=ctx.run_id, system=self._system_name)
        try:
            result = await app.ainvoke(
                cast(
                    Any,
                    _new_state(
                        message,
                        history=history,
                        run_context=_state_run_context(ctx) if has_caller_context else None,
                    ),
                ),
                config,
            )
            pending = await self._pending_result(ctx, result)
            if pending is not None:
                return pending
            run_result = RunResult(
                system_name=self._system_name,
                visited=result.get("visited", []),
                answer=result.get("answer", ""),
                used_tools=tuple(result.get("used_tools", [])),
                input_tokens=input_tokens or None,
                output_tokens=output_tokens or None,
            )
            await self._complete_run(ctx.run_id)
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
            await self._fail_run(ctx.run_id)
            await hook_manager.run_run_error(ctx, exc)
            raise
        finally:
            current_run_context.reset(token)
            current_execution.reset(exec_token)
            current_streams.reset(stream_token)

    # ------------------------------------------------------------------ #
    # Human-in-the-Loop: run/approval lifecycle                          #
    # ------------------------------------------------------------------ #

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

    async def _register_run(self, ctx: RunContext) -> None:
        assert ctx.run_id is not None
        if await self._approval_manager.get_run_or_none(ctx.run_id) is None:
            await self._approval_manager.register_run(
                RunRecord(
                    run_id=ctx.run_id,
                    thread_id=ctx.run_id,
                    system_name=self._system_name,
                    status=RunStatus.RUNNING,
                )
            )

    async def _complete_run(self, run_id: str | None) -> None:
        if run_id is None:
            return
        run = await self._approval_manager.get_run_or_none(run_id)
        if run is not None and run.status in (RunStatus.RUNNING, RunStatus.RESUMING):
            await self._approval_manager.mark_run(run_id, RunStatus.COMPLETED)

    async def _fail_run(self, run_id: str | None) -> None:
        if run_id is None:
            return
        run = await self._approval_manager.get_run_or_none(run_id)
        if run is not None and run.status not in (RunStatus.COMPLETED, RunStatus.FAILED):
            await self._approval_manager.mark_run(run_id, RunStatus.FAILED)

    async def _pending_result(self, ctx: RunContext, result: Any) -> RunResult | None:
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
            used_tools=tuple(result.get("used_tools", [])),
            status="pending_approval",
            pending_approval=_pending_approval(approval),
        )

    async def get_run_status(self, run_id: str) -> str:
        """Return the current status of a run (raises RunNotFound if unknown)."""
        run = await self._approval_manager.get_run(run_id)
        return run.status.value

    async def get_pending_approval(self, run_id: str) -> PendingApproval | None:
        """Return the run's outstanding approval, or None if there is none."""
        approval = await self._approval_manager.get_pending(run_id)
        return _pending_approval(approval) if approval is not None else None

    async def resume(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
    ) -> RunResult:
        """Apply a human decision to a pending tool call and resume the same run.

        Atomically claims the approval (exactly one caller wins across pods),
        resumes the existing LangGraph thread from its checkpoint — so the agent
        is not re-selected and completed steps are not intentionally redone — and
        either executes or denies the original tool call. ``ALLOW_FOR_SESSION``
        additionally records a session permission so the tool is not re-prompted
        for the rest of the conversation.
        """
        app, hook_manager = self._require_built("resuming")
        kind = parse_decision(decision)
        approval = await self._approval_manager.claim(
            run_id=run_id, approval_id=approval_id, caller_user_id=caller_user_id
        )
        approved = kind != ApprovalDecision.DENY
        ctx = RunContext(
            run_id=run_id,
            conversation_id=approval.auth_ref,
            user_id=approval.authorized_user_id,
            organization_id=approval.organization_id,
            metadata={"approval_id": approval.approval_id},
        )
        token = current_run_context.set(ctx)
        exec_token = current_execution.set(ExecutionLimiter(self._policy))
        log(
            logger,
            logging.INFO,
            "resume started",
            run_id=run_id,
            approval_id=approval_id,
            decision=kind.value,
        )
        try:
            result = await app.ainvoke(
                Command(resume={"decision": kind.value}),
                self._thread_config(ctx),
            )
            await self._approval_manager.finalize(approval_id, approved=approved)
            pending = await self._pending_result(ctx, result)
            if pending is not None:
                # The resumed run hit a *further* approval; stay pending.
                return pending
            await self._complete_run(run_id)
            run_result = RunResult(
                system_name=self._system_name,
                visited=result.get("visited", []),
                answer=result.get("answer", ""),
                used_tools=tuple(result.get("used_tools", [])),
                status="completed",
            )
            await hook_manager.run_run_end(
                ctx, _run_end_context(self._system_name, ctx, run_result)
            )
            log(
                logger,
                logging.INFO,
                "run completed",
                run_id=run_id,
                approval_id=approval_id,
                decision=kind.value,
            )
            return run_result
        except Exception as exc:
            await self._fail_run(run_id)
            await hook_manager.run_run_error(ctx, exc)
            raise
        finally:
            current_run_context.reset(token)
            current_execution.reset(exec_token)

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        app, hook_manager = self._require_built("streaming")

        has_caller_context = context is not None
        ctx = await self._begin_run(context)
        queue: asyncio.Queue[RunStreamEvent | BaseException | None] = asyncio.Queue()
        input_tokens = 0
        output_tokens = 0

        def accumulate_tokens(inp: int, out: int) -> None:
            nonlocal input_tokens, output_tokens
            input_tokens += inp
            output_tokens += out

        state = _new_state(
            message,
            history=history,
            run_context=_state_run_context(ctx) if has_caller_context else None,
        )
        sinks = StreamSinks(
            answer=lambda c: queue.put_nowait(RunStreamEvent(type="answer_delta", content=c)),
            route=lambda r: queue.put_nowait(RunStreamEvent(type="route", route=r)),
            token=accumulate_tokens,
        )

        await self._register_run(ctx)

        async def run_graph() -> None:
            config = self._thread_config(ctx)
            try:
                result = await app.ainvoke(cast(Any, state), config)
                pending = await self._pending_result(ctx, result)
                if pending is not None and pending.pending_approval is not None:
                    pa = pending.pending_approval
                    queue.put_nowait(
                        RunStreamEvent(
                            type="pending_approval",
                            route=tuple(pending.visited),
                            system_name=self._system_name,
                            used_tools=pending.used_tools,
                            run_id=pa.run_id,
                            approval_id=pa.approval_id,
                            agent_id=pa.agent_id,
                            tool_name=pa.tool_name,
                            description=pa.description,
                        )
                    )
                    return
                await self._complete_run(ctx.run_id)
                visited = tuple(result.get("visited", []))
                used_tools = tuple(result.get("used_tools", []))
                queue.put_nowait(
                    RunStreamEvent(
                        type="final",
                        content=result.get("answer", ""),
                        route=visited,
                        system_name=self._system_name,
                        used_tools=used_tools,
                        input_tokens=input_tokens or None,
                        output_tokens=output_tokens or None,
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
                await self._fail_run(ctx.run_id)
                await hook_manager.run_run_error(ctx, exc)
                queue.put_nowait(exc)
            finally:
                queue.put_nowait(None)

        log(logger, logging.INFO, "run started", run_id=ctx.run_id, system=self._system_name)
        token = current_run_context.set(ctx)
        exec_token = current_execution.set(ExecutionLimiter(self._policy))
        # Set before creating the task so the child task inherits the sinks.
        stream_token = current_streams.set(sinks)
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
            current_execution.reset(exec_token)
            current_streams.reset(stream_token)

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
        # A checkpointer is always present (in-memory by default), so approval
        # interrupts and resume use one identical code path regardless of backend.
        assert self._checkpointer is not None
        return builder.compile(checkpointer=self._checkpointer.saver)

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
        model = self._build_model(spec.model)

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
        model = self._build_model(spec.model)
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
            execution_manager=self._execution_manager,
            approval_coordinator=self._approval_coordinator,
            system_namespace=self._system_name,
        )

    def _build_model(self, model: NodeModelConfig) -> BaseChatModel:
        return self._model_factory(
            model.provider,
            model.name,
            model.temperature,
            **_model_factory_kwargs(self._model_factory, model),
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
