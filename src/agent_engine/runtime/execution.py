"""Runtime enforcement of the :class:`ExecutionPolicy`.

A single :class:`ExecutionLimiter` is created per run and published on the
``current_execution`` context var (the same pattern as ``current_run_context``),
so every node, tool call, and child invocation can consult it without threading
state through the graph.

When a limit is hit the limiter raises :class:`ExecutionLimitExceeded` carrying
only safe metadata (limit name, count, configured limit, node/agent/tool ids).
Callers catch it at the seam and degrade gracefully — never crashing the run:
the tool loop stops, and a blocked tool/child call returns a controlled message
the model can read. Raw prompts, arguments, results, and secrets are never logged.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field

from agent_engine.core.execution import ExecutionPolicy
from agent_engine.logging_config import log
from agent_engine.runtime.hooks.context import current_run_context

logger = logging.getLogger(__name__)


class ExecutionLimitExceeded(Exception):
    """A configured execution limit was reached. Carries safe metadata only."""

    def __init__(
        self,
        limit_name: str,
        count: int,
        limit: int,
        *,
        node_id: str | None = None,
        agent_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        self.limit_name = limit_name
        self.count = count
        self.limit = limit
        self.node_id = node_id
        self.agent_id = agent_id
        self.tool_name = tool_name
        super().__init__(f"execution limit '{limit_name}' reached ({count} > {limit})")


@dataclass
class ExecutionState:
    """Mutable per-run counters. Never holds prompts, arguments, or results —
    only counts and opaque call signatures used for duplicate detection."""

    iterations_by_node: dict[str, int] = field(default_factory=dict)
    total_tool_calls: int = 0
    tool_calls_by_agent: dict[str, int] = field(default_factory=dict)
    child_agent_calls: int = 0
    seen_signatures: set[str] = field(default_factory=set)


class ExecutionLimiter:
    """Enforces an :class:`ExecutionPolicy` for the duration of one run."""

    def __init__(self, policy: ExecutionPolicy) -> None:
        self._policy = policy
        self._state = ExecutionState()

    @property
    def policy(self) -> ExecutionPolicy:
        return self._policy

    @property
    def state(self) -> ExecutionState:
        return self._state

    def register_iteration(self, node_id: str) -> None:
        """Count one model→tools→model round for ``node_id``. Raises when that
        node exceeds ``max_iterations``."""
        count = self._state.iterations_by_node.get(node_id, 0) + 1
        self._state.iterations_by_node[node_id] = count
        if count > self._policy.max_iterations:
            raise ExecutionLimitExceeded(
                "max_iterations", count, self._policy.max_iterations, node_id=node_id
            )

    def register_tool_call(self, agent_id: str, tool_name: str, args: object) -> None:
        """Authorize one local/MCP tool call. Raises on a blocked duplicate, the
        per-run total, or the per-agent limit. Blocked calls are NOT counted."""
        signature = _signature(agent_id, tool_name, args)
        if not self._policy.allow_duplicate_tool_calls and signature in self._state.seen_signatures:
            raise ExecutionLimitExceeded(
                "duplicate_tool_call", 1, 0, agent_id=agent_id, tool_name=tool_name
            )
        total = self._state.total_tool_calls + 1
        if total > self._policy.max_tool_calls:
            raise ExecutionLimitExceeded(
                "max_tool_calls",
                total,
                self._policy.max_tool_calls,
                agent_id=agent_id,
                tool_name=tool_name,
            )
        per_agent = self._state.tool_calls_by_agent.get(agent_id, 0) + 1
        if per_agent > self._policy.max_tool_calls_per_agent:
            raise ExecutionLimitExceeded(
                "max_tool_calls_per_agent",
                per_agent,
                self._policy.max_tool_calls_per_agent,
                agent_id=agent_id,
                tool_name=tool_name,
            )
        # Commit only once every check passed.
        self._state.total_tool_calls = total
        self._state.tool_calls_by_agent[agent_id] = per_agent
        self._state.seen_signatures.add(signature)

    def register_child_call(self, parent_node_id: str, child_id: str) -> None:
        """Authorize one orchestrator→child-agent call. Raises on the per-run
        ``max_child_agent_calls`` limit."""
        count = self._state.child_agent_calls + 1
        if count > self._policy.max_child_agent_calls:
            raise ExecutionLimitExceeded(
                "max_child_agent_calls",
                count,
                self._policy.max_child_agent_calls,
                node_id=parent_node_id,
                agent_id=child_id,
            )
        self._state.child_agent_calls = count


#: Per-run limiter, published like ``current_run_context``. ``None`` when no
#: engine run is active (e.g. a unit test calling a node directly).
current_execution: ContextVar[ExecutionLimiter | None] = ContextVar(
    "current_execution", default=None
)


def _signature(agent_id: str, tool_name: str, args: object) -> str:
    """Opaque, stable signature for duplicate detection: agent + tool + args.

    Arguments are serialized deterministically but the result is only ever stored
    in a set and compared — never logged."""
    try:
        serialized = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        serialized = repr(args)
    return f"{agent_id}\x1f{tool_name}\x1f{serialized}"


def log_limit(exc: ExecutionLimitExceeded) -> None:
    """Log a limit hit with safe metadata only (no prompts/args/results/secrets)."""
    ctx = current_run_context.get()
    log(
        logger,
        logging.WARNING,
        "execution limit reached",
        run_id=getattr(ctx, "run_id", None),
        node_id=exc.node_id,
        agent=exc.agent_id,
        tool=exc.tool_name,
        limit=exc.limit_name,
        count=exc.count,
        configured=exc.limit,
    )


def blocked_message(exc: ExecutionLimitExceeded) -> str:
    """A controlled, model-readable result for a blocked tool/child call."""
    if exc.limit_name == "duplicate_tool_call":
        return (
            "Tool call blocked: an identical call (same arguments) was already made "
            "in this run; duplicate tool calls are disabled by the execution policy. "
            "Use the previous result instead of repeating the call."
        )
    return (
        f"Call blocked: the '{exc.limit_name}' execution limit ({exc.limit}) was "
        "reached for this run. Do not retry; finish with the information you have."
    )
