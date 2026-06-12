from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from langchain_core.tools import BaseTool, StructuredTool

from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.tool_models import RuntimeTool

logger = logging.getLogger(__name__)


class LangChainToolAdapterError(RuntimeError):
    """Raised when runtime tools cannot be adapted or executed."""


class RuntimeToolCaller(Protocol):
    async def call_tool(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object: ...


def build_langchain_tools_from_runtime_tools(
    *,
    agent_id: str,
    runtime_tools: Sequence[RuntimeTool],
    tool_registry: RuntimeToolCaller,
    ctx: ExecutionContext,
) -> list[BaseTool]:
    """Adapt source-agnostic runtime tool metadata into executable LangChain tools."""
    _ensure_unique_runtime_tool_names(agent_id, runtime_tools)

    logger.debug("Adapting runtime tools for agent=%s count=%d", agent_id, len(runtime_tools))
    return [
        _build_langchain_tool(
            agent_id=agent_id,
            runtime_tool=runtime_tool,
            tool_registry=tool_registry,
            ctx=ctx,
        )
        for runtime_tool in runtime_tools
    ]


def ensure_no_duplicate_tool_names(
    *,
    agent_id: str,
    local_tools: Sequence[BaseTool],
    runtime_tools: Sequence[RuntimeTool],
) -> None:
    """Fail before binding if local and runtime tools would expose the same name."""
    seen: dict[str, str] = {}

    for tool in local_tools:
        seen[tool.name] = "local plugin"

    for runtime_tool in runtime_tools:
        existing = seen.get(runtime_tool.name)
        if existing is not None:
            raise LangChainToolAdapterError(
                f"Agent '{agent_id}' cannot bind duplicate tool name "
                f"'{runtime_tool.name}' from {existing} and runtime ToolRegistry."
            )
        seen[runtime_tool.name] = "runtime ToolRegistry"


def _build_langchain_tool(
    *,
    agent_id: str,
    runtime_tool: RuntimeTool,
    tool_registry: RuntimeToolCaller,
    ctx: ExecutionContext,
) -> BaseTool:
    def run_tool(**arguments: object) -> str:
        logger.debug("Executing adapted tool=%s for agent=%s", runtime_tool.name, agent_id)
        try:
            result = _run_async_tool_call(
                tool_registry.call_tool(
                    agent_id=agent_id,
                    tool_name=runtime_tool.name,
                    arguments=dict(arguments),
                    ctx=ctx,
                )
            )
        except Exception as exc:
            logger.error("Adapted tool=%s failed for agent=%s", runtime_tool.name, agent_id)
            raise LangChainToolAdapterError(
                f"Tool '{runtime_tool.name}' failed for agent '{agent_id}': {exc}"
            ) from exc

        logger.debug(
            "Adapted tool=%s succeeded for agent=%s result_type=%s",
            runtime_tool.name,
            agent_id,
            type(result).__name__,
        )
        return normalize_tool_result(result)

    async def arun_tool(**arguments: object) -> str:
        logger.debug("Executing adapted tool=%s for agent=%s", runtime_tool.name, agent_id)
        try:
            result = await tool_registry.call_tool(
                agent_id=agent_id,
                tool_name=runtime_tool.name,
                arguments=dict(arguments),
                ctx=ctx,
            )
        except Exception as exc:
            logger.error("Adapted tool=%s failed for agent=%s", runtime_tool.name, agent_id)
            raise LangChainToolAdapterError(
                f"Tool '{runtime_tool.name}' failed for agent '{agent_id}': {exc}"
            ) from exc

        logger.debug(
            "Adapted tool=%s succeeded for agent=%s result_type=%s",
            runtime_tool.name,
            agent_id,
            type(result).__name__,
        )
        return normalize_tool_result(result)

    return StructuredTool.from_function(
        func=run_tool,
        coroutine=arun_tool,
        name=runtime_tool.name,
        description=runtime_tool.description,
        args_schema=runtime_tool.parameters_schema,
        infer_schema=False,
    )


def normalize_tool_result(result: object) -> str:
    """Return model-safe tool content for LangChain ``ToolMessage`` payloads."""
    if isinstance(result, str):
        return result

    if isinstance(result, dict | list):
        return json.dumps(result, sort_keys=True, separators=(",", ":"))

    if result is None or isinstance(result, bool | int | float):
        return json.dumps(result)

    return str(result)


def _ensure_unique_runtime_tool_names(
    agent_id: str,
    runtime_tools: Sequence[RuntimeTool],
) -> None:
    seen: set[str] = set()
    for runtime_tool in runtime_tools:
        if runtime_tool.name in seen:
            raise LangChainToolAdapterError(
                f"Agent '{agent_id}' cannot bind duplicate runtime tool name '{runtime_tool.name}'."
            )
        seen.add(runtime_tool.name)


def _run_async_tool_call(awaitable: Coroutine[Any, Any, object]) -> object:
    # The current LangGraph node loop is synchronous, while ToolRegistry is
    # async because MCP clients are async. Keep that bridge centralized here.
    if _is_event_loop_running():
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(awaitable)).result()

    return asyncio.run(awaitable)


def _is_event_loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
