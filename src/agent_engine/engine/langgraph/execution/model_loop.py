"""Model invocation, streaming, and iterative tool-call execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.func import task

from agent_engine.engine.langgraph.checkpointing import CheckpointReplayMismatch, in_graph_run
from agent_engine.engine.langgraph.execution.execution_context import (
    ExecutionContextRefresher,
)
from agent_engine.engine.langgraph.execution.model_context import ModelContext
from agent_engine.runtime.execution_limiter import (
    ITERATION_LIMIT_ANSWER,
    ExecutionLimitExceeded,
    blocked_message,
    current_execution,
    log_limit,
)
from agent_engine.runtime.streaming import current_streams
from agent_engine.runtime.tool_results import NormalizedToolResult
from agent_engine.tool_usage.models import stable_tool_call_id

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
    """Drive model → tools → model, restoring completed steps on HITL replay.

    Model outputs determine the interrupt order and must never be regenerated.
    ToolInvoker checkpoints leaf outcomes after registering execution limits.
    Orchestrator delegations rebuild their route locally and rely on the child's
    checkpointed model/tool steps instead. Restored outputs must match this node
    and its original input, even if route filtering changes the replay order.
    """
    checkpointed = in_graph_run()
    step_key = stable_tool_call_id(
        node_path,
        [
            {"type": message.type, "content": message.content}
            for message in context.messages
            if not isinstance(message, SystemMessage)
        ],
    )

    @task
    async def model_step(turn: int) -> tuple[str, int, Any]:
        await _refresh(context, refresh_execution_context)
        return step_key, turn, await invoke_model(model, context.messages)

    model_turn = 0

    async def next_response() -> Any:
        nonlocal model_turn
        if checkpointed:
            restored_key, restored_turn, response = await model_step(model_turn)
            if (restored_key, restored_turn) != (step_key, model_turn):
                raise CheckpointReplayMismatch(node_path)
            model_turn += 1
            return response
        await _refresh(context, refresh_execution_context)
        return await invoke_model(model, context.messages)

    limiter = current_execution.get()
    response = await next_response()
    while getattr(response, "tool_calls", None):
        if limiter is not None:
            try:
                limiter.register_iteration(node_path)
            except ExecutionLimitExceeded as exc:
                log_limit(exc)
                return await _wrap_up(response, exc, context, next_response)
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
        response = await next_response()
    return response


async def _wrap_up(
    response: Any,
    exc: ExecutionLimitExceeded,
    context: ModelContext,
    next_response: Callable[[], Awaitable[Any]],
) -> Any:
    """Give the model one turn to answer after ``max_iterations`` is reached.

    The pending tool calls are answered with the same controlled message the
    other limits use, without executing them, so the model can finish with what
    it has. A wrap-up turn that still produces no text yields a fixed answer
    instead of an empty one.
    """
    context.append(response)
    for tool_call in response.tool_calls:
        context.append(
            ToolMessage(
                content=blocked_message(exc),
                tool_call_id=tool_call["id"],
                name=tool_call["name"],
            )
        )
    final = await next_response()
    if as_text(final.content).strip():
        return final
    return AIMessage(content=ITERATION_LIMIT_ANSWER)


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
