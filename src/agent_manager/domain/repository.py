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
    Page,
    PageRequest,
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
    async def link_anonymous_user(self, anonymous_user_id: str, user_id: str) -> int:
        """Hand a visitor's conversations to the account they just signed into.

        Returns how many moved. Linking happens once: a replayed visitor pass
        moves nothing and returns 0, so the same conversations can never be
        attached to a second account.
        """

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
    ) -> ConversationSession:
        """Create the session, or return it untouched if the id is taken.

        Every field here describes a session being born, so a taken id writes
        nothing at all — not the owner, and not the title, config or expiry.
        Otherwise naming a live id would be enough to take a conversation over,
        or to rebind someone else's to another system. Later changes go through
        the explicit operations, `rename_session` and the rest.
        """

    @abstractmethod
    async def get_session(self, session_id: str) -> ConversationSession | None: ...

    @abstractmethod
    async def list_sessions(
        self, user_id: str, page: PageRequest | None = None
    ) -> Page[ConversationSession]:
        """A user's sessions, most-recently-active first, with cursor pagination."""

    @abstractmethod
    async def rename_session(self, session_id: str, title: str) -> None: ...

    @abstractmethod
    async def append_message(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> ConversationSnapshot: ...

    @abstractmethod
    async def append_message_if_absent(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> bool:
        """Atomically append by ``message_id`` and report whether it was created.

        A message whose parent is no longer the selected head is retained on
        that inactive branch without changing the conversation's active head.
        """
        ...

    @abstractmethod
    async def append_message_if_head(
        self,
        message: ConversationMessage,
        expected_head_message_id: str | None,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> bool:
        """Append and select ``message`` only if the active branch head matches.

        The comparison and append are one atomic repository operation. A false
        result writes nothing and lets the application reject concurrent turns
        or edits without producing a detached execution.
        """
        ...

    @abstractmethod
    async def get_message(self, message_id: str) -> ConversationMessage | None: ...

    @abstractmethod
    async def get_user_message_for_run(
        self, session_id: str, run_id: str
    ) -> ConversationMessage | None:
        """Find the run's user message across active and inactive branches."""
        ...

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
    ) -> ConversationContext: ...

    @abstractmethod
    async def get_context_at(
        self,
        session_id: str,
        head_message_id: str | None,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
    ) -> ConversationContext:
        """Build context from one immutable ancestry path; ``None`` is empty."""
        ...

    @abstractmethod
    async def record_run_token_usage(
        self, conversation_id: str, run_id: str, total_tokens: int
    ) -> None:
        """Retain a run's cumulative usage, independently of its messages.

        Repeated or out-of-order observations keep the highest total. The
        charge survives branch edits and expiry of engine lifecycle records.
        """

    async def get_token_usage(self, conversation_id: str) -> int:
        """Lifetime tokens across all branches, counting each run once.

        Message usage remains the fallback for historical runs. A recorded
        run total replaces, rather than adds to, that run's message usage.
        """
        return sum((await self.get_token_usage_by_run(conversation_id)).values())

    @abstractmethod
    async def get_token_usage_by_run(self, conversation_id: str) -> dict[str | None, int]:
        """Lifetime message/ledger totals, including zero-usage runs on every branch.

        The ``None`` key groups legacy messages without a run id. Run ids must
        belong to this conversation so callers can reconcile authoritative runs.
        """

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
