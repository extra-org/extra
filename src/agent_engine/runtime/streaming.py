from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_engine.runtime.tool_models import ToolProviderName, ToolUsageRecord, ToolUsageStatus

RunStreamEventType = Literal[
    "route",
    "answer_delta",
    "tool_started",
    "tool_succeeded",
    "tool_failed",
    "final",
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
