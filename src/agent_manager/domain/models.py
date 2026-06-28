"""Domain value objects — what the business logic speaks. No framework imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


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
