from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from agent_engine.runtime.tool_models import ToolProviderName, ToolUsageRecord, ToolUsageStatus


@dataclass
class TokenUsage:
    """Per-run token counters fed by the ``token`` sink below.

    Mutable and deliberately trivial: it exists so a run's accounting is one
    object that can be handed to the sink and read back when the run finishes,
    instead of closure variables that only the enclosing function can see.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def totals(self) -> tuple[int | None, int | None]:
        """Counted tokens, or ``None`` when a model never reported usage.

        ``None`` (rather than ``0``) distinguishes "not reported" from "zero"
        for result and event payloads.
        """
        return self.input_tokens or None, self.output_tokens or None


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
    "resume_started",
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
    run_id: str | None = None
    approval_id: str | None = None
    agent_id: str | None = None
    description: str | None = None
    arguments: dict[str, Any] | None = None
    message_id: str | None = None
