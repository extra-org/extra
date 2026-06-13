from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class MessageRole(Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str


ToolUsageStatus = Literal["started", "succeeded", "failed"]
ToolProviderName = Literal["local", "mcp", "unknown"]


@dataclass(frozen=True)
class ToolUsageRecord:
    name: str
    provider: ToolProviderName
    status: ToolUsageStatus
    agent_id: str
    server_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RunResult:
    system_name: str
    visited: list[str]
    answer: str
    used_tools: tuple[ToolUsageRecord, ...] = field(default_factory=tuple)
