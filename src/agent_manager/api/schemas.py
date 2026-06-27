"""HTTP request/response shapes — the API contract.

`ToolRecord`/`SendMessageResponse` mirror the engine's run result so it passes
through unchanged.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from agent_manager.domain import Role


class CreateConversationResponse(BaseModel):
    conversation_id: str


class MessageOut(BaseModel):
    role: Role
    content: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    message: str


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
