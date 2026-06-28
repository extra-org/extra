"""In-memory repository. For tests and a second concrete proof of the port."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from agent_manager.domain import (
    ConversationContext,
    ConversationMessage,
    ConversationSession,
    ConversationSnapshot,
    Message,
    Repository,
    Role,
    User,
)


class MemoryRepository(Repository):
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._sessions: dict[str, ConversationSession] = {}
        self._messages: dict[str, list[ConversationMessage]] = {}
        self._snapshots: dict[str, ConversationSnapshot] = {}

    async def upsert_user(
        self,
        user_id: str,
        *,
        external_user_id: str | None = None,
        username: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> User:
        now = datetime.now(UTC)
        existing = self._users.get(user_id)
        user = User(
            user_id=user_id,
            external_user_id=external_user_id
            if external_user_id is not None
            else existing.external_user_id
            if existing
            else None,
            username=username if username is not None else existing.username if existing else None,
            display_name=display_name
            if display_name is not None
            else existing.display_name
            if existing
            else None,
            metadata=dict(metadata or (existing.metadata if existing else {})),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._users[user_id] = user
        return user

    async def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

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
        sid = session_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        existing = self._sessions.get(sid)
        if existing is not None:
            return existing
        session = ConversationSession(
            session_id=sid,
            user_id=user_id,
            system_name=system_name,
            config_path=config_path,
            title=title,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self._sessions[sid] = session
        self._messages.setdefault(sid, [])
        return session

    async def get_session(self, session_id: str) -> ConversationSession | None:
        return self._sessions.get(session_id)

    async def create_conversation(self) -> str:
        return (await self.create_session()).session_id

    async def conversation_exists(self, conversation_id: str) -> bool:
        return conversation_id in self._sessions

    async def append_message(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> ConversationSnapshot:
        if message.session_id not in self._sessions:
            await self.create_session(message.session_id, user_id=message.user_id)
        self._messages.setdefault(message.session_id, []).append(message)
        session = self._sessions[message.session_id]
        self._sessions[message.session_id] = ConversationSession(
            session_id=session.session_id,
            user_id=session.user_id or message.user_id,
            system_name=session.system_name,
            config_path=session.config_path,
            title=session.title,
            metadata=session.metadata,
            created_at=session.created_at,
            updated_at=message.created_at,
            last_message_at=message.created_at,
            expires_at=session.expires_at,
        )
        snapshot = self._build_snapshot(message.session_id, snapshot_ttl_seconds)
        self._snapshots[message.session_id] = snapshot
        return snapshot

    async def add_message(self, conversation_id: str, role: Role, content: str) -> None:
        await self.append_message(
            ConversationMessage(
                message_id=uuid.uuid4().hex,
                session_id=conversation_id,
                role=role,
                content=content,
                created_at=datetime.now(UTC),
            )
        )

    async def list_conversation_messages(
        self, session_id: str, limit: int | None = None
    ) -> list[ConversationMessage]:
        msgs = self._messages.get(session_id, [])
        return list(msgs[-limit:] if limit is not None else msgs)

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        msgs = await self.list_conversation_messages(conversation_id, limit)
        return [Message(role=m.role, content=m.content, created_at=m.created_at) for m in msgs]

    async def get_snapshot(self, session_id: str) -> ConversationSnapshot | None:
        return self._snapshots.get(session_id)

    async def rebuild_snapshot(
        self, session_id: str, *, snapshot_ttl_seconds: int | None = None
    ) -> ConversationSnapshot | None:
        if session_id not in self._sessions:
            return None
        snapshot = self._build_snapshot(session_id, snapshot_ttl_seconds)
        self._snapshots[session_id] = snapshot
        return snapshot

    async def get_context(
        self,
        session_id: str,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
        max_tokens: int | None = None,
    ) -> ConversationContext:
        del max_tokens
        snapshot = self._snapshots.get(session_id)
        source = "snapshot"
        if snapshot is None:
            snapshot = await self.rebuild_snapshot(session_id)
            source = "rebuilt"
        messages = await self.list_messages(session_id)
        messages = _bound_messages(messages, max_messages=max_messages, max_chars=max_chars)
        return ConversationContext(
            session_id=session_id,
            messages=messages,
            message_count=len(self._messages.get(session_id, [])),
            source=source if snapshot is not None else "cold",
            snapshot=snapshot,
        )

    async def delete_expired_snapshots(self, now: datetime) -> int:
        expired = [
            session_id
            for session_id, snapshot in self._snapshots.items()
            if snapshot.expires_at is not None and snapshot.expires_at <= now
        ]
        for session_id in expired:
            del self._snapshots[session_id]
        return len(expired)

    def _build_snapshot(
        self, session_id: str, snapshot_ttl_seconds: int | None
    ) -> ConversationSnapshot:
        from datetime import timedelta

        now = datetime.now(UTC)
        messages = self._messages.get(session_id, [])
        last = messages[-1] if messages else None
        conversation_json: dict[str, Any] = {
            "messages": [
                {
                    "message_id": msg.message_id,
                    "run_id": msg.run_id,
                    "role": msg.role.value,
                    "content": msg.content,
                    "content_type": msg.content_type,
                    "created_at": msg.created_at.isoformat(),
                    "metadata": deepcopy(msg.metadata),
                }
                for msg in messages
            ]
        }
        session = self._sessions[session_id]
        expires_at = (
            now + timedelta(seconds=snapshot_ttl_seconds)
            if snapshot_ttl_seconds is not None
            else None
        )
        return ConversationSnapshot(
            session_id=session_id,
            user_id=session.user_id,
            conversation_json=conversation_json,
            message_count=len(messages),
            last_message_id=last.message_id if last else None,
            last_message_at=last.created_at if last else None,
            updated_at=now,
            expires_at=expires_at,
        )


def _bound_messages(
    messages: list[Message], *, max_messages: int | None, max_chars: int | None
) -> list[Message]:
    bounded = messages[-max_messages:] if max_messages is not None else list(messages)
    if max_chars is None:
        return bounded
    total = 0
    kept: list[Message] = []
    for msg in reversed(bounded):
        size = len(msg.content)
        if kept and total + size > max_chars:
            break
        kept.append(msg)
        total += size
        if total >= max_chars:
            break
    return list(reversed(kept))
