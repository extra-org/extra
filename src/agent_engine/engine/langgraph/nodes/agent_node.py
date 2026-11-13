"""Leaf-agent execution inside the compiled LangGraph runtime."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from agent_engine.core.spec import AgentSpec
from agent_engine.engine.langgraph.execution.execution_context import (
    execution_context_refresher,
)
from agent_engine.engine.langgraph.execution.model_context import ModelContext
from agent_engine.engine.langgraph.execution.model_loop import as_text, emit_route, run_tool_loop
from agent_engine.engine.langgraph.execution.node_executor import NodeExecutor
from agent_engine.engine.langgraph.prompting import (
    EXTRA_DEFAULT_SYSTEM_PROMPT,
    assemble_prompt,
    load_file,
    render_prompt,
    resolve_prompt_context,
)
from agent_engine.engine.langgraph.tools.tool_invoker import ToolInvoker
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.runtime.execution_limiter import current_invocation
from agent_engine.runtime.state import GraphState
from agent_engine.tool_usage.context_provider import ToolUsageContextProvider

logger = logging.getLogger(__name__)


class AgentNode(NodeExecutor):
    """Execute one agent turn with explicitly injected dependencies."""

    def __init__(
        self,
        spec: AgentSpec,
        node_path: str,
        bound_model: Any,
        resolver_loader: ResolverLoader,
        base_dir: Path,
        tool_invoker: ToolInvoker,
        usage_context: ToolUsageContextProvider,
    ) -> None:
        self._spec = spec
        self._node_path = node_path
        self._bound_model = bound_model
        self._resolver_loader = resolver_loader
        self._base_dir = base_dir
        self._tool_invoker = tool_invoker
        self._refresh_execution_context = execution_context_refresher(usage_context, spec.id)

    async def execute(self, state: GraphState) -> GraphState:
        token = current_invocation.set(uuid.uuid4().hex)
        try:
            ctx = resolve_prompt_context(self._resolver_loader, self._spec)
            system_prompt = self._build_prompt(ctx)
            return await self._run(system_prompt, state)
        finally:
            current_invocation.reset(token)

    def _build_prompt(self, ctx: dict[str, str]) -> str:
        """Compose: Extra default (unless opted out) → rendered agent prompt."""
        prompts = self._spec.prompts
        template = load_file(self._base_dir, prompts.system) or self._spec.description
        return assemble_prompt(
            EXTRA_DEFAULT_SYSTEM_PROMPT if prompts.extra_default else None,
            render_prompt(template, ctx),
        )

    async def _run(self, system_prompt: str, state: GraphState) -> GraphState:
        user_msg: str = state.get("message", "")
        context = ModelContext(system_prompt, state.get("history", []), user_msg)
        logger.debug("[%s] system:\n%s", self._node_path, system_prompt)
        logger.debug("[%s] → user: %s", self._node_path, user_msg)

        visited: list[str] = [*state.get("visited", []), self._node_path]
        emit_route(tuple(visited))
        response = await run_tool_loop(
            self._bound_model,
            context,
            self._node_path,
            self._tool_invoker.invoke,
            refresh_execution_context=self._refresh_execution_context,
        )
        answer = as_text(response.content)
        logger.debug("[%s] ← response: %s", self._node_path, answer[:300])
        return {"visited": visited, "answer": answer}
