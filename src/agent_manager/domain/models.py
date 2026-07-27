"""Domain value objects — what the business logic speaks. No framework imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

CONTEXT_WARNING_PERCENT = 65.0
CONTEXT_CRITICAL_PERCENT = 85.0


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    ORCHESTRATOR = "orchestrator"
    AGENT = "agent"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    created_at: datetime
    message_id: str | None = None
    feedback: str | None = None


@dataclass(frozen=True)
class User:
    user_id: str
    external_user_id: str | None = None
    username: str | None = None
    display_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ConversationSession:
    session_id: str
    user_id: str | None = None
    system_name: str | None = None
    config_path: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_message_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    session_id: str
    role: Role
    content: str
    created_at: datetime
    run_id: str | None = None
    user_id: str | None = None
    node_id: str | None = None
    agent_id: str | None = None
    parent_message_id: str | None = None
    content_type: str = "text"
    tool_name: str | None = None
    provider: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    status: str = "succeeded"
    error_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    feedback: str | None = None


@dataclass(frozen=True)
class ConversationSnapshot:
    session_id: str
    user_id: str | None
    conversation_json: dict[str, Any]
    message_count: int
    last_message_id: str | None
    last_message_at: datetime | None
    updated_at: datetime
    model_context_tokens: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ConversationContext:
    session_id: str
    messages: list[Message]
    message_count: int
    source: str
    snapshot: ConversationSnapshot | None = None


def thread_title(content: str, *, limit: int = 48) -> str:
    text = " ".join(content.split())
    if not text:
        return "New chat"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class ContextSeverity(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ContextUsage:
    used_tokens: int
    max_tokens: int | None
    percent: float
    severity: ContextSeverity

    @classmethod
    def from_totals(cls, used_tokens: int, max_tokens: int | None) -> ContextUsage:
        if not max_tokens:
            return cls(used_tokens, max_tokens, 0.0, ContextSeverity.NORMAL)
        percent = min(used_tokens / max_tokens * 100, 100.0)
        if percent > CONTEXT_CRITICAL_PERCENT:
            severity = ContextSeverity.CRITICAL
        elif percent >= CONTEXT_WARNING_PERCENT:
            severity = ContextSeverity.WARNING
        else:
            severity = ContextSeverity.NORMAL
        return cls(used_tokens, max_tokens, percent, severity)
