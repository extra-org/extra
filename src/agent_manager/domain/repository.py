"""The persistence port. Adapters implement it; the application depends on it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from agent_manager.domain.models import (
    ConversationContext,
    ConversationMessage,
    ConversationSession,
    ConversationSnapshot,
    Message,
    Role,
    User,
)


class Repository(ABC):
    @abstractmethod
    async def upsert_user(
        self,
        user_id: str,
        *,
        external_user_id: str | None = None,
        username: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> User: ...

    @abstractmethod
    async def get_user(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def create_session(
        self,
        session_id: str | None = None,
        *,
        user_id: str | None = None,
        system_name: str | None = None,
        config_path: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> ConversationSession: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> ConversationSession | None: ...

    @abstractmethod
    async def append_message(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> ConversationSnapshot: ...

    @abstractmethod
    async def list_conversation_messages(
        self, session_id: str, limit: int | None = None
    ) -> list[ConversationMessage]:
        """Messages oldest-first. With `limit`, the most recent `limit`, still
        oldest-first."""

    @abstractmethod
    async def get_snapshot(self, session_id: str) -> ConversationSnapshot | None: ...

    @abstractmethod
    async def rebuild_snapshot(
        self, session_id: str, *, snapshot_ttl_seconds: int | None = None
    ) -> ConversationSnapshot | None: ...

    @abstractmethod
    async def get_context(
        self,
        session_id: str,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
        max_tokens: int | None = None,
    ) -> ConversationContext: ...

    @abstractmethod
    async def delete_expired_snapshots(self, now: datetime) -> int: ...

    async def create_conversation(self) -> str:
        """Backward-compatible alias: a conversation is a stable session."""
        return (await self.create_session()).session_id

    @abstractmethod
    async def conversation_exists(self, conversation_id: str) -> bool: ...

    async def add_message(self, conversation_id: str, role: Role, content: str) -> None:
        from datetime import UTC
        from uuid import uuid4

        await self.append_message(
            ConversationMessage(
                message_id=uuid4().hex,
                session_id=conversation_id,
                role=role,
                content=content,
                created_at=datetime.now(UTC),
            )
        )

    @abstractmethod
    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        """Messages oldest-first. With `limit`, the most recent `limit`, still
        oldest-first."""
