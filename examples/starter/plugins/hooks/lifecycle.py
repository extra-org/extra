"""Hook plugin — one method per lifecycle point, each one deliberately trivial.

Run the example and watch these fire in order. Where each method logs, your own
code would do the real work: audit, metrics, redaction, tracing, cache warming.

Two rules worth keeping:

* Hooks are trusted application code, but they are not a security boundary for
  the model. Never log secrets, tool arguments, tool results or user content.
* Hold no per-request state on the instance — it is shared across every
  concurrent request. Read what you need from the event.
"""

from __future__ import annotations

import logging

from agent_engine.runtime.hooks import (
    HookInvocation,
    RunContext,
    ToolCallContext,
    ToolResultContext,
)

logger = logging.getLogger("starter")

# Results longer than this are trimmed before they reach the model, so one
# chatty tool cannot blow up the prompt.
_MAX_RESULT_CHARS = 2000


class LifecycleHook:
    def __init__(self) -> None:
        # Long-lived, request-independent state is fine here: clients, caches,
        # configuration. Per-request values are not.
        self._runs = 0

    async def run_started(self, event: HookInvocation) -> RunContext:
        """on_run_start — fires once per request, before any routing.

        Whatever this returns replaces the run context for the rest of the run,
        which is where a host application injects identity. Returning it
        unchanged, as here, is a no-op.
        """
        ctx = event.payload_as(RunContext)
        self._runs += 1
        logger.info("run started run_id=%s total_runs=%d", ctx.run_id, self._runs)
        return ctx

    async def tool_finished(self, event: HookInvocation) -> None:
        """after_tool_call — fires after every tool, MCP or local."""
        call = event.payload_as(ToolCallContext)
        logger.info(
            "tool done agent=%s tool=%s provider=%s status=%s ms=%s",
            call.agent_id,
            call.tool_name,
            call.provider,
            call.status,
            call.latency_ms,
        )

    async def shorten_result(self, event: HookInvocation) -> ToolResultContext:
        """transform_tool_result — the returned value is what the model sees.

        This is the hook to reach for when you need to redact, reformat or cap a
        tool result before it enters the conversation.
        """
        result = event.payload_as(ToolResultContext)
        if len(result.result) <= _MAX_RESULT_CHARS:
            return result
        logger.info(
            "result trimmed tool=%s original_chars=%d kept_chars=%d",
            result.tool_name,
            len(result.result),
            _MAX_RESULT_CHARS,
        )
        return result.with_result(result.result[:_MAX_RESULT_CHARS] + "\n\n[trimmed]")

    async def run_failed(self, event: HookInvocation) -> None:
        """on_run_error — the error type only. Messages can carry user data."""
        error = event.payload_as(BaseException)
        run_id = event.run_context.run_id if event.run_context else None
        logger.warning("run failed run_id=%s error_type=%s", run_id, type(error).__name__)
