from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_engine.runtime.tool_models import ToolUsageRecord


class ChatRole(StrEnum):
    """Roles accepted as structured conversation history by the engine."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ChatMessage:
    """One prior conversational turn supplied to a stateless engine run."""

    role: ChatRole
    content: str


@dataclass(frozen=True)
class PendingApproval:
    """A run suspended awaiting a human decision on a tool call.

    Carries only sanitized, non-secret fields — safe to return to a UI.
    """

    run_id: str
    approval_id: str
    agent_id: str
    tool_name: str
    description: str
    provider: str = "local"
    server_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """The outcome of one run: the route taken, the answer, and the tools
    observed along the way.

    ``status`` is ``"completed"`` for a finished run or ``"pending_approval"``
    when the run is suspended at an approval interrupt (in which case
    ``pending_approval`` is populated and ``answer`` is empty). Defaulting
    ``status`` keeps existing construction sites and callers backward compatible.
    """

    system_name: str
    visited: list[str]
    answer: str
    used_tools: tuple[ToolUsageRecord, ...] = field(default_factory=tuple)
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: str = "completed"
    pending_approval: PendingApproval | None = None
