"""In-memory repository. For tests and a second concrete proof of the port."""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_manager.domain import (
    ConversationContext,
    ConversationMessage,
    ConversationSession,
    ConversationSnapshot,
    Message,
    Page,
    PageRequest,
    Repository,
    Role,
    User,
)
from agent_manager.domain.pagination import (
    decode_cursor,
    encode_cursor,
    ensure_utc,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _effective_t(s: ConversationSession) -> datetime:
    dt = s.last_message_at or s.created_at or _EPOCH
    res = ensure_utc(dt)
    return res if res is not None else _EPOCH


def _is_after_cursor(s: ConversationSession, cursor_t: datetime, cursor_id: str) -> bool:
    target_t = ensure_utc(cursor_t) or _EPOCH
    eff_t = _effective_t(s)
    if eff_t < target_t:
        return True
    if eff_t == target_t:
        return (s.session_id or "") < cursor_id
    return False


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
        existing = self._users.get(user_id)
        now = datetime.now(UTC)
        user = User(
            user_id=user_id,
            external_user_id=external_user_id
            if external_user_id is not None
            else (existing.external_user_id if existing else None),
            username=username
            if username is not None
            else (existing.username if existing else None),
            display_name=display_name
            if display_name is not None
            else (existing.display_name if existing else None),
            linked_to_user_id=existing.linked_to_user_id if existing else None,
            metadata=dict(
                metadata if metadata is not None else (existing.metadata if existing else {})
            ),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._users[user_id] = user
        return user

    async def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    async def link_anonymous_user(self, anonymous_user_id: str, user_id: str) -> int:
        if anonymous_user_id == user_id:
            return 0

        target = self._users.get(user_id)
        if target and target.linked_to_user_id:
            return 0

        anon = self._users.get(anonymous_user_id)
        if anon and anon.linked_to_user_id:
            return 0

        moved = 0
        now = datetime.now(UTC)
        for sid, session in list(self._sessions.items()):
            if session.user_id == anonymous_user_id:
                self._sessions[sid] = replace(session, user_id=user_id, updated_at=now)
                moved += 1

        if anon:
            self._users[anonymous_user_id] = replace(
                anon, linked_to_user_id=user_id, updated_at=now
            )

        return moved

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
        sid = session_id or uuid4().hex
        if sid in self._sessions:
            return self._sessions[sid]

        now = datetime.now(UTC)
        session = ConversationSession(
            session_id=sid,
            user_id=user_id,
            system_name=system_name,
            config_path=config_path,
            title=title,
            head_message_id=None,
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

    async def list_sessions(
        self, user_id: str, page: PageRequest | None = None
    ) -> Page[ConversationSession]:
        page = page or PageRequest()
        limit = page.limit
        cursor = page.cursor
        sessions = [s for s in self._sessions.values() if s.user_id == user_id]
        sessions.sort(
            key=lambda s: (_effective_t(s), s.session_id or ""),
            reverse=True,
        )

        if cursor is not None:
            cursor_t, cursor_id = decode_cursor(cursor)
            sessions = [s for s in sessions if _is_after_cursor(s, cursor_t, cursor_id)]

        has_more = len(sessions) > limit
        result_sessions = sessions[:limit] if has_more else sessions
        next_cursor = (
            encode_cursor(
                _effective_t(result_sessions[-1]),
                result_sessions[-1].session_id,
            )
            if has_more and result_sessions
            else None
        )
        return Page(items=result_sessions, next_cursor=next_cursor)

    async def rename_session(self, session_id: str, title: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions[session_id] = replace(session, title=title)

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
        session = self._sessions[message.session_id]
        if message.parent_message_id is None and session.head_message_id is not None:
            message = replace(message, parent_message_id=session.head_message_id)
        self._messages.setdefault(message.session_id, []).append(message)
        self._sessions[message.session_id] = replace(
            session,
            head_message_id=message.message_id,
            updated_at=message.created_at,
            last_message_at=message.created_at,
        )
        snapshot = self._build_snapshot(message.session_id, snapshot_ttl_seconds)
        self._snapshots[message.session_id] = snapshot
        return snapshot

    async def append_message_if_absent(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> bool:
        """Append once under the adapter's single-event-loop execution model."""
        if any(
            existing.message_id == message.message_id
            for existing in self._messages.get(message.session_id, [])
        ):
            return False
        session = self._sessions.get(message.session_id)
        if session is not None and session.head_message_id != message.parent_message_id:
            self._messages.setdefault(message.session_id, []).append(message)
            return True
        await self.append_message(message, snapshot_ttl_seconds=snapshot_ttl_seconds)
        return True

    async def append_message_if_head(
        self,
        message: ConversationMessage,
        expected_head_message_id: str | None,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> bool:
        session = self._sessions.get(message.session_id)
        if session is None or session.head_message_id != expected_head_message_id:
            return False
        self._messages.setdefault(message.session_id, []).append(message)
        self._sessions[message.session_id] = replace(
            session,
            head_message_id=message.message_id,
            updated_at=message.created_at,
            last_message_at=message.created_at,
        )
        self._snapshots[message.session_id] = self._build_snapshot(
            message.session_id, snapshot_ttl_seconds
        )
        return True

    async def get_message(self, message_id: str) -> ConversationMessage | None:
        return next(
            (
                message
                for messages in self._messages.values()
                for message in messages
                if message.message_id == message_id
            ),
            None,
        )

    async def get_user_message_for_run(
        self, session_id: str, run_id: str
    ) -> ConversationMessage | None:
        return next(
            (
                message
                for message in self._messages.get(session_id, [])
                if message.run_id == run_id and message.role == Role.USER
            ),
            None,
        )

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
        session = self._sessions.get(session_id)
        msgs = _branch_messages(
            self._messages.get(session_id, []),
            session.head_message_id if session is not None else None,
        )
        if limit is None:
            return list(msgs)
        return list(msgs[-limit:]) if limit > 0 else []

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        msgs = await self.list_conversation_messages(conversation_id, limit)
        return [Message(role=m.role, content=m.content, created_at=m.created_at) for m in msgs]

    async def update_message_feedback(
        self,
        message_id: str,
        feedback: str,
    ) -> ConversationMessage | None:
        for _sid, messages in list(self._messages.items()):
            for index, message in enumerate(messages):
                if message.message_id == message_id:
                    metadata = {**message.metadata, "feedback": feedback}
                    updated = replace(message, feedback=feedback, metadata=metadata)
                    messages[index] = updated
                    return updated
        return None

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

    async def get_token_usage(self, conversation_id: str) -> int:
        return sum(
            (m.input_tokens or 0) + (m.output_tokens or 0)
            for m in self._messages.get(conversation_id, [])
        )

    async def get_context(
        self,
        session_id: str,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
    ) -> ConversationContext:
        snapshot = self._snapshots.get(session_id)
        source = "snapshot"
        if snapshot is None:
            snapshot = await self.rebuild_snapshot(session_id)
            source = "rebuilt"
        active_messages = await self.list_messages(session_id)
        messages = _bound_messages(active_messages, max_messages=max_messages, max_chars=max_chars)
        return ConversationContext(
            session_id=session_id,
            messages=messages,
            message_count=len(active_messages),
            source=source if snapshot is not None else "cold",
            snapshot=snapshot,
        )

    async def get_context_at(
        self,
        session_id: str,
        head_message_id: str | None,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
    ) -> ConversationContext:
        messages = _branch_messages(self._messages.get(session_id, []), head_message_id)
        context = [
            Message(role=m.role, content=m.content, created_at=m.created_at) for m in messages
        ]
        bounded = _bound_messages(context, max_messages=max_messages, max_chars=max_chars)
        return ConversationContext(
            session_id=session_id,
            messages=bounded,
            message_count=len(messages),
            source="branch",
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
        session = self._sessions[session_id]
        messages = _branch_messages(self._messages.get(session_id, []), session.head_message_id)
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


def _branch_messages(
    messages: list[ConversationMessage], head_message_id: str | None
) -> list[ConversationMessage]:
    """Return the selected immutable root-to-head ancestry path."""
    if head_message_id is None:
        return []
    by_id = {message.message_id: message for message in messages}
    path: list[ConversationMessage] = []
    cursor = by_id.get(head_message_id)
    while cursor is not None:
        path.append(cursor)
        cursor = by_id.get(cursor.parent_message_id) if cursor.parent_message_id else None
    path.reverse()
    return path


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
