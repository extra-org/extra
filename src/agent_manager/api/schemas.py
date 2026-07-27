"""HTTP request/response shapes — the API contract.

`ToolRecord`/`SendMessageResponse` mirror the engine's run result so it passes
through unchanged.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from agent_manager.domain import ContextSeverity, Role


class CreateConversationRequest(BaseModel):
    session_id: str | None = None
    user_id: str | None = None


class CreateConversationResponse(BaseModel):
    conversation_id: str
    session_id: str


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str | None = None
    last_message_at: datetime | None = None


class MessageOut(BaseModel):
    role: Role
    content: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    message: str
    user_id: str | None = None


class ToolRecord(BaseModel):
    name: str
    provider: str
    status: str
    agent_id: str | None = None
    server_id: str | None = None
    error: str | None = None


class SendMessageResponse(BaseModel):
    answer: str
    visited: list[str]
    used_tools: list[ToolRecord]


class ContextUsageResponse(BaseModel):
    used_tokens: int
    max_tokens: int | None = None
    percent: float = 0.0
    severity: ContextSeverity = ContextSeverity.NORMAL


class StreamEventOut(BaseModel):
    type: str
    content: str | None = None
    route: list[str] | None = None
    tool_name: str | None = None
    provider: str | None = None
    server_id: str | None = None
    status: str | None = None
    error: str | None = None
    system_name: str | None = None
    used_tools: list[ToolRecord] | None = None
