"""Model invocation, streaming, and iterative tool-call execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent_engine.engine.langgraph.execution.execution_context import (
    ExecutionContextRefresher,
)
from agent_engine.engine.langgraph.execution.model_context import ModelContext
from agent_engine.runtime.execution_limiter import (
    ExecutionLimitExceeded,
    current_execution,
    log_limit,
)
from agent_engine.runtime.streaming import current_streams
from agent_engine.runtime.tool_results import NormalizedToolResult

logger = logging.getLogger(__name__)


async def invoke_model(model: Any, messages: list[Any]) -> Any:
    sinks = current_streams.get()
    answer_stream = sinks.answer
    if answer_stream is None:
        response = await model.ainvoke(messages)
        usage = getattr(response, "usage_metadata", None)
        if sinks.token is not None and usage:
            sinks.token(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        return response

    streamed = None
    try:
        async for chunk in model.astream(messages):
            streamed = chunk if streamed is None else streamed + chunk
            text = as_text(getattr(chunk, "content", ""))
            if text:
                answer_stream(text)
    finally:
        usage = getattr(streamed, "usage_metadata", None)
        if sinks.token is not None and usage:
            sinks.token(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return streamed or AIMessage(content="")


async def run_tool_loop(
    model: Any,
    context: ModelContext,
    node_path: str,
    invoke_tool: Callable[[dict[str, Any]], Awaitable[str | NormalizedToolResult]],
    *,
    refresh_execution_context: ExecutionContextRefresher | None = None,
) -> Any:
    """Drive model → tools → model until the model stops calling tools."""
    limiter = current_execution.get()
    await _refresh(context, refresh_execution_context)
    response = await invoke_model(model, context.messages)
    while getattr(response, "tool_calls", None):
        if limiter is not None:
            try:
                limiter.register_iteration(node_path)
            except ExecutionLimitExceeded as exc:
                log_limit(exc)
                break
        context.append(response)
        for tool_call in response.tool_calls:
            logger.debug(
                "[%s] ← tool_call: %s(arguments=%d)",
                node_path,
                tool_call["name"],
                len(tool_call.get("args") or {}),
            )
            raw_result = await invoke_tool(tool_call)
            result = (
                raw_result
                if isinstance(raw_result, NormalizedToolResult)
                else NormalizedToolResult.text_only(raw_result)
            )
            logger.debug(
                "[%s] → tool_result[%s] chars=%d structured=%s artifact=%s",
                node_path,
                tool_call["name"],
                len(result.text),
                result.has_structured,
                result.has_artifact,
            )
            context.append(
                ToolMessage(
                    content=result.text,
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                )
            )
        await _refresh(context, refresh_execution_context)
        response = await invoke_model(model, context.messages)
    return response


async def _refresh(
    context: ModelContext,
    refresh_execution_context: ExecutionContextRefresher | None,
) -> None:
    if refresh_execution_context is not None:
        context.set_execution_context(await refresh_execution_context())


def emit_route(route: tuple[str, ...]) -> None:
    sink = current_streams.get().route
    if sink is not None:
        sink(route)


def as_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return str(content)
