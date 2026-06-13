"""Request-scoped execution context passed to resolver plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Per-request context visible to customer-owned resolver methods.

    Resolver outputs are stored on ``resolved_context`` for the duration of one
    graph invocation. The runtime creates a fresh instance while preparing each
    agent node execution.
    """

    message: str
    state: dict[str, Any]
    resolved_context: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    trace_events: list[dict[str, Any]] = field(default_factory=list)

    def add_trace_event(self, event: dict[str, Any]) -> None:
        self.trace_events.append(event)
