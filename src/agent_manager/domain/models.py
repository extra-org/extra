"""Domain value objects — what the business logic speaks. No framework imports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")

THREAD_TITLE_LIMIT = 48
BUDGET_WARNING_PERCENT = 65.0
BUDGET_CRITICAL_PERCENT = 85.0


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
    # Set when a visitor's conversations were handed to an account, so the same
    # pass can never be linked twice.
    linked_to_user_id: str | None = None
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
    head_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_message_at: datetime | None = None
    expires_at: datetime | None = None


DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


@dataclass(frozen=True)
class PageRequest:
    limit: int = DEFAULT_PAGE_LIMIT
    cursor: str | None = None

    def __post_init__(self) -> None:
        bounded_limit = max(1, min(self.limit, MAX_PAGE_LIMIT))
        object.__setattr__(self, "limit", bounded_limit)
        if self.cursor is not None and not self.cursor.strip():
            object.__setattr__(self, "cursor", None)


@dataclass(frozen=True)
class Page(Generic[T]):
    items: Sequence[T]
    next_cursor: str | None = None


PaginatedSessions = Page[ConversationSession]


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


def compact_text(content: str) -> str:
    """One clean line: no newlines, no runs of whitespace."""
    return " ".join(content.split())


def thread_title(content: str, *, limit: int = THREAD_TITLE_LIMIT) -> str:
    text = compact_text(content)
    if not text:
        return "New chat"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class BudgetSeverity(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TokenBudgetUsage:
    """How much of a conversation's lifetime token budget has been spent.

    This is cumulative consumption — every turn's input + output tokens summed
    over the whole conversation — measured against `context_max_tokens`, the
    budget the service enforces (see `ConversationTokenBudgetExceeded`). It is
    deliberately *not* the size of the context window currently sent to the
    model: history is re-sent each turn, so the same message is counted again
    every time it is included.
    """

    used_tokens: int
    max_tokens: int | None
    percent: float
    severity: BudgetSeverity

    @classmethod
    def from_totals(cls, used_tokens: int, max_tokens: int | None) -> TokenBudgetUsage:
        if not max_tokens:
            return cls(used_tokens, max_tokens, 0.0, BudgetSeverity.NORMAL)
        percent = min(used_tokens / max_tokens * 100, 100.0)
        if percent >= BUDGET_CRITICAL_PERCENT:
            severity = BudgetSeverity.CRITICAL
        elif percent >= BUDGET_WARNING_PERCENT:
            severity = BudgetSeverity.WARNING
        else:
            severity = BudgetSeverity.NORMAL
        return cls(used_tokens, max_tokens, percent, severity)
