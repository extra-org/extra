"""Supervisor-agent execution inside the compiled LangGraph runtime."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel

from agent_engine.approvals.errors import ApprovalInvocationMismatch
from agent_engine.core.spec import OrchestratorSpec
from agent_engine.engine.langgraph.checkpointing import CheckpointReplayMismatch
from agent_engine.engine.langgraph.execution.execution_context import (
    current_usage_scope,
    execution_context_refresher,
)
from agent_engine.engine.langgraph.execution.model_context import ModelContext
from agent_engine.engine.langgraph.execution.model_loop import as_text, emit_route, run_tool_loop
from agent_engine.engine.langgraph.execution.node_executor import NodeExecutor
from agent_engine.engine.langgraph.filters import RouteFilter
from agent_engine.engine.langgraph.nodes.child_entry import ChildEntry
from agent_engine.engine.langgraph.prompting import (
    EXTRA_DEFAULT_SYSTEM_PROMPT,
    assemble_prompt,
    load_file,
    render_prompt,
    resolve_prompt_context,
)
from agent_engine.engine.langgraph.tools.executed_tools_tool import (
    EXECUTED_TOOLS_TOOL_NAME,
    build_executed_tools_tool,
)
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.runtime.execution_limiter import (
    ExecutionLimitExceeded,
    blocked_message,
    current_execution,
    current_invocation,
    log_limit,
)
from agent_engine.runtime.state import GraphState
from agent_engine.runtime.streaming import current_streams
from agent_engine.tool_usage.context_provider import ToolUsageContextProvider
from agent_engine.tool_usage.models import (
    ToolCallIdentity,
    ToolInvocationKind,
    stable_tool_call_id,
)
from agent_engine.tool_usage.tracker import ToolUsageTracker

logger = logging.getLogger(__name__)

_ORCHESTRATOR_CONTRACT = """
## Instructions
- You MUST use the available agent tools to answer requests. Never answer from general knowledge.
- Only call a tool if its name/description clearly matches the request.
  Do NOT call a tool for something outside its stated scope.
- If no appropriate tool exists for part of the request, say: "I'm not able to help with that."
- You may call multiple tools if the request covers several topics.
- The tools available to you are other AGENTS, not actions. Calling one hands the request
  to that agent; the agent, not you, performs the work with its own tools. Never describe an
  agent you called as a tool that ran.
- When the user asks which tools have run or what has already been done, call
  `list_executed_tools` and answer from its result — never from your own tool list.
"""


class _AgentCall(BaseModel):
    """Input schema for a child-agent tool."""

    message: str


class OrchestratorNode(NodeExecutor):
    """Supervisor-pattern orchestrator.

    Child agents (AgentNode or nested OrchestratorNode) are exposed as tools
    to the orchestrator LLM.  The LLM reads its system prompt, decides which
    tool(s) to call, collects their answers, and synthesises a final response.

    Access filters control which child tools are made available — if a child is
    filtered out the LLM simply does not have that tool and responds naturally
    (e.g. "I can't help with domestic flights").

    Like an agent, it receives the conversation's tool usage as private context,
    refreshed before each model turn — so its synthesis knows what the children
    it just called actually ran, and it can answer a user asking what has been
    done so far.
    """

    def __init__(
        self,
        spec: OrchestratorSpec,
        node_path: str,
        model: BaseChatModel,
        children: list[ChildEntry],
        filters: list[RouteFilter],
        resolver_loader: ResolverLoader,
        base_dir: Path,
        usage_context: ToolUsageContextProvider,
        usage_tracker: ToolUsageTracker,
        fallback_model: BaseChatModel | None = None,
    ) -> None:
        self._spec = spec
        self._node_path = node_path
        self._model = model
        self._fallback_model = fallback_model
        self._children = children
        self._filters = filters
        self._resolver_loader = resolver_loader
        self._base_dir = base_dir
        self._usage = usage_tracker
        self._usage_context = usage_context
        self._refresh_execution_context = execution_context_refresher(usage_context, spec.id)

    async def execute(self, state: GraphState) -> GraphState:
        token = current_invocation.set(uuid.uuid4().hex)
        try:
            candidates = self._filter_children(state)
            ctx = resolve_prompt_context(self._resolver_loader, self._spec)
            system_prompt = self._build_prompt(ctx)
            return await self._run(system_prompt, candidates, state)
        finally:
            current_invocation.reset(token)

    def _build_prompt(self, ctx: dict[str, str]) -> str:
        """Compose: Extra default (unless opted out) → rendered prompts → contract.

        Both prompt templates are rendered as one template so a variable
        resolves identically wherever it appears. The Extra default and the
        engine's routing contract are engine text and never interpolated.
        """
        prompts = self._spec.prompts
        base = load_file(self._base_dir, prompts.system) or self._spec.description
        orchestrator = load_file(self._base_dir, prompts.orchestrator)
        return assemble_prompt(
            EXTRA_DEFAULT_SYSTEM_PROMPT if prompts.extra_default else None,
            render_prompt(assemble_prompt(base, orchestrator), ctx),
            # Engine text, so trimming it is safe; custom prompts are never trimmed.
            _ORCHESTRATOR_CONTRACT.strip(),
        )

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
    ) -> StructuredTool:
        """Wrap a child node as a StructuredTool the orchestrator LLM can call.

        ``visited_acc`` is the parent's live route list: when the child returns we
        merge its new path segments back into it, so the final route reflects the
        whole call-chain. The child's tool usage needs no merging — it is already
        recorded against the same run in the usage repository.

        Delegating to a child is itself an action this orchestrator took, so it
        is recorded too — that is how a later turn knows the routing that already
        happened. Execution still goes through the child node, unchanged.
        """

        async def invoke(message: str) -> str:
            snapshot = list(visited_acc)
            identity = self._child_identity(entry, message)
            # Children never stream their answer to the user — only the root
            # orchestrator's final synthesis does. The stream sinks are ambient
            # (current_streams); clear the answer sink for the child while keeping
            # route/token, so the live route still reflects the full chain.
            sub_state: GraphState = {
                "message": message,
                "visited": snapshot,
                "run_context": parent_state.get("run_context", {}),
            }
            child_sinks = replace(current_streams.get(), answer=None)
            sink_token = current_streams.set(child_sinks)
            try:
                result = await entry.node.execute(sub_state)
            except (GraphInterrupt, ApprovalInvocationMismatch, CheckpointReplayMismatch):
                # An approval interrupt raised inside a nested child must bubble up
                # to the LangGraph runtime so the checkpoint is taken — it is
                # control flow, not a child failure. Replay/consent mismatches
                # also abort the run instead of becoming model-facing tool text.
                raise
            except Exception as exc:
                await self._usage.record_failure(identity, error=str(exc))
                return f"Agent error: {exc}"
            finally:
                current_streams.reset(sink_token)

            for path in result.get("visited", [])[len(snapshot) :]:
                visited_acc.append(path)
            await self._usage.record_success(identity)
            return result.get("answer", "")

        return StructuredTool.from_function(
            coroutine=invoke,
            name=entry.id,
            description=entry.tool_description,
            args_schema=_AgentCall,
        )

    def _reporting_tools(self, child_names: set[str]) -> list[BaseTool]:
        """The engine's own read-only tools, added unless a child claims the name.

        A configured child always wins: the system's own graph outranks a
        convenience the engine provides. Callers add these only when the
        orchestrator has children to expose, so an orchestrator whose children
        were all filtered out still faces the model with no tools at all.
        """
        if EXECUTED_TOOLS_TOOL_NAME in child_names:
            return []
        return [build_executed_tools_tool(self._usage_context, self._spec.id)]

    def _child_identity(self, entry: ChildEntry, message: str) -> ToolCallIdentity:
        """Name one delegation the same way a tool call is named.

        Derived from the run, this orchestrator's position, the child, and the
        message, so a replay after resume reaches the same identity instead of
        recording a second delegation.
        """
        scope = current_usage_scope(self._spec.id)
        run_id = scope.run_id or self._node_path
        return ToolCallIdentity(
            run_id=run_id,
            agent_id=self._spec.id,
            agent_path=self._node_path,
            tool_call_id=stable_tool_call_id(run_id, self._node_path, "agent", entry.id, message),
            tool_name=entry.id,
            conversation_id=scope.conversation_id,
            kind=ToolInvocationKind.AGENT,
        )

    async def _run(
        self,
        system_prompt: str,
        candidates: list[ChildEntry],
        state: GraphState,
    ) -> GraphState:
        """Drive the orchestrator LLM tool loop and return the synthesised answer."""
        visited: list[str] = [*state.get("visited", []), self._node_path]

        # Build tools here so they share the live `visited` list.
        child_tools = [self._make_tool(e, state, visited) for e in candidates]
        child_names = {tool.name for tool in child_tools}
        tools = [*child_tools, *self._reporting_tools(child_names)] if child_tools else []
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
        context = ModelContext(system_prompt, state.get("history", []), user_msg)
        logger.debug(
            "[%s] system:\n%s\ntools: %s", self._node_path, system_prompt, list(tool_by_name)
        )
        logger.debug("[%s] → user: %s", self._node_path, user_msg)

        emit_route(tuple(visited))

        async def invoke_tool(tc: dict[str, Any]) -> str:
            tool = tool_by_name.get(tc["name"])
            if tool is None:
                return f"Unknown agent: {tc['name']}"
            # Limit orchestrator→child-agent invocations. A blocked call returns a
            # controlled message instead of running the child. Reporting on past
            # execution is not a delegation, so it is not counted or recorded.
            limiter = current_execution.get()
            if limiter is not None and tc["name"] in child_names:
                try:
                    limiter.register_child_call(self._node_path, tc["name"])
                except ExecutionLimitExceeded as exc:
                    log_limit(exc)
                    return blocked_message(exc)
            return cast(str, await tool.ainvoke(tc["args"]))

        response = await run_tool_loop(
            bound_model,
            context,
            self._node_path,
            invoke_tool,
            refresh_execution_context=self._refresh_execution_context,
        )
        answer = as_text(response.content)
        logger.debug("[%s] ← response: %s", self._node_path, answer[:300])

        return {"visited": visited, "answer": answer}
