from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from agent_engine.runtime.tool_models import ToolProviderName, ToolUsageRecord, ToolUsageStatus


@dataclass(frozen=True)
class StreamSinks:
    """Request-scoped streaming callbacks.

    Held on a contextvar rather than in ``GraphState`` because the graph state is
    serialized by the checkpointer and callables are not serializable. Ambient
    per-run sinks keep the state a plain, persistable data structure.
    """

    answer: Callable[[str], None] | None = None
    route: Callable[[tuple[str, ...]], None] | None = None
    token: Callable[[int, int], None] | None = None


# StreamSinks is a frozen (immutable) dataclass, so a shared default instance is
# safe here despite B039's generic warning about mutable ContextVar defaults.
current_streams: ContextVar[StreamSinks] = ContextVar("current_streams", default=StreamSinks())  # noqa: B039

RunStreamEventType = Literal[
    "route",
    "answer_delta",
    "tool_started",
    "tool_succeeded",
    "tool_failed",
    "final",
    "pending_approval",
]


@dataclass(frozen=True)
class RunStreamEvent:
    """Platform-level event emitted while a run is executing."""

    type: RunStreamEventType
    content: str | None = None
    route: tuple[str, ...] | None = None
    tool_name: str | None = None
    provider: ToolProviderName | None = None
    server_id: str | None = None
    status: ToolUsageStatus | None = None
    error: str | None = None
    system_name: str | None = None
    used_tools: tuple[ToolUsageRecord, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Populated only on a ``pending_approval`` event, when the run suspends at an
    # approval interrupt. All fields are sanitized and safe to send to a client.
    run_id: str | None = None
    approval_id: str | None = None
    agent_id: str | None = None
    description: str | None = None
