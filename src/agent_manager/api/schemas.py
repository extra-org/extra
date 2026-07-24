"""HTTP request/response shapes — the API contract.

`ToolRecord`/`SendMessageResponse` mirror the engine's run result so it passes
through unchanged.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agent_manager.domain import Role


class CreateConversationRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)


class CreateConversationResponse(BaseModel):
    conversation_id: str
    session_id: str


class MessageOut(BaseModel):
    role: Role
    content: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    message: str = Field(max_length=65536)
    user_id: str | None = Field(default=None, max_length=128)


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
