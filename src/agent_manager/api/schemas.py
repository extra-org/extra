"""HTTP request/response shapes — the API contract.

`ToolRecord`/`SendMessageResponse` mirror the engine's run result so it passes
through unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_engine.approvals.decision import ApprovalDecision
from agent_manager.domain import BudgetSeverity, Role


class AnonymousPassResponse(BaseModel):
    """A visitor pass the widget stores and sends back as a bearer token."""

    token: str
    expires_at: datetime


class LinkAnonymousRequest(BaseModel):
    """The visitor pass whose conversations the signed-in caller is adopting."""

    anonymous_token: str


class LinkAnonymousResponse(BaseModel):
    conversations_moved: int


class CreateConversationRequest(BaseModel):
    session_id: str | None = None


class CreateConversationResponse(BaseModel):
    conversation_id: str
    session_id: str


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str | None = None
    last_message_at: datetime | None = None


class PaginatedConversationsResponse(BaseModel):
    items: list[ConversationSummary]
    next_cursor: str | None = None


class MessageOut(BaseModel):
    message_id: str
    run_id: str | None = None
    role: Role
    content: str
    status: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    message: str
    edit_message_id: str | None = None


class ToolRecord(BaseModel):
    name: str
    provider: str
    status: str
    agent_id: str | None = None
    server_id: str | None = None
    error: str | None = None


class PendingApprovalOut(BaseModel):
    run_id: str
    approval_id: str
    agent_id: str
    tool_name: str
    description: str
    provider: str
    server_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class SendMessageResponse(BaseModel):
    answer: str
    visited: list[str]
    used_tools: list[ToolRecord]
    status: str = "completed"
    run_id: str | None = None
    pending_approval: PendingApprovalOut | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision


class CancelRunResponse(BaseModel):
    run_id: str
    status: str = "cancelled"


class TokenBudgetResponse(BaseModel):
    used_tokens: int
    max_tokens: int | None = None
    percent: float = 0.0
    severity: BudgetSeverity = BudgetSeverity.NORMAL


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
    run_id: str | None = None
    message_id: str | None = None
    approval_id: str | None = None
    agent_id: str | None = None
    description: str | None = None
    arguments: dict[str, Any] | None = None
