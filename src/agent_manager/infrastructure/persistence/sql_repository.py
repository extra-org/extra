"""SQL-backed conversation repository.

SQLite and PostgreSQL are selected by SQLAlchemy URL/driver. The application
layer depends only on the repository port, so backend details stay here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

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
from agent_manager.infrastructure.persistence.tables import (
    ConversationMessageRow,
    ConversationSessionRow,
    ConversationSnapshotRow,
    ConversationUserRow,
)


class SqlRepository(Repository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

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
        async with self._sessions() as session, session.begin():
            row = await session.get(ConversationUserRow, user_id)
            if row is None:
                row = ConversationUserRow(
                    user_id=user_id,
                    external_user_id=external_user_id,
                    username=username,
                    display_name=display_name,
                    metadata_json=dict(metadata or {}),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if external_user_id is not None:
                    row.external_user_id = external_user_id
                if username is not None:
                    row.username = username
                if display_name is not None:
                    row.display_name = display_name
                if metadata is not None:
                    row.metadata_json = dict(metadata)
                row.updated_at = now
        return _user(row)

    async def get_user(self, user_id: str) -> User | None:
        async with self._sessions() as session:
            row = await session.get(ConversationUserRow, user_id)
        return _user(row) if row else None

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
        async with self._sessions() as session, session.begin():
            row = await session.get(ConversationSessionRow, sid)
            if row is None:
                row = ConversationSessionRow(
                    session_id=sid,
                    user_id=user_id,
                    system_name=system_name,
                    config_path=config_path,
                    title=title,
                    metadata_json=dict(metadata or {}),
                    created_at=now,
                    updated_at=now,
                    expires_at=expires_at,
                )
                session.add(row)
            elif any(
                value is not None
                for value in (user_id, system_name, config_path, title, metadata, expires_at)
            ):
                row.user_id = user_id if user_id is not None else row.user_id
                row.system_name = system_name if system_name is not None else row.system_name
                row.config_path = config_path if config_path is not None else row.config_path
                row.title = title if title is not None else row.title
                row.metadata_json = dict(metadata) if metadata is not None else row.metadata_json
                row.expires_at = expires_at if expires_at is not None else row.expires_at
                row.updated_at = now
        return _session(row)

    async def get_session(self, session_id: str) -> ConversationSession | None:
        async with self._sessions() as session:
            row = await session.get(ConversationSessionRow, session_id)
        return _session(row) if row else None

    # `create_conversation`/`add_message` are not overridden here: the
    # `Repository` base class already provides them as thin aliases over
    # `create_session`/`append_message` (see domain/repository.py), which is
    # exactly the current schema's behavior. An earlier version additionally
    # wrote to the retired `conversations`/`messages` tables for backward
    # compatibility; that write path was removed in the same change that
    # dropped those tables (migration 0003) once nothing read them back.

    async def conversation_exists(self, conversation_id: str) -> bool:
        async with self._sessions() as session:
            return await session.get(ConversationSessionRow, conversation_id) is not None

    # -- rich conversation persistence -------------------------------------

    async def append_message(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> ConversationSnapshot:
        async with self._sessions() as session, session.begin():
            session_row = await session.get(ConversationSessionRow, message.session_id)
            if session_row is None:
                session_row = ConversationSessionRow(
                    session_id=message.session_id,
                    user_id=message.user_id,
                    created_at=message.created_at,
                    updated_at=message.created_at,
                    last_message_at=message.created_at,
                    metadata_json={},
                )
                session.add(session_row)
            else:
                if session_row.user_id is None and message.user_id is not None:
                    session_row.user_id = message.user_id
                session_row.updated_at = message.created_at
                session_row.last_message_at = message.created_at

            session.add(_message_row(message))
            await session.flush()
            snapshot = await self._rebuild_snapshot_in_session(
                session, message.session_id, snapshot_ttl_seconds=snapshot_ttl_seconds
            )
            assert snapshot is not None
            return snapshot

    async def list_conversation_messages(
        self, session_id: str, limit: int | None = None
    ) -> list[ConversationMessage]:
        async with self._sessions() as session:
            rows = await self._message_rows(session, session_id, limit)
        return [_message(row) for row in rows]

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        rows = await self.list_conversation_messages(conversation_id, limit)
        return [
            Message(role=row.role, content=row.content, created_at=row.created_at) for row in rows
        ]

    async def get_snapshot(self, session_id: str) -> ConversationSnapshot | None:
        async with self._sessions() as session:
            row = await session.get(ConversationSnapshotRow, session_id)
        return _snapshot(row) if row else None

    async def rebuild_snapshot(
        self, session_id: str, *, snapshot_ttl_seconds: int | None = None
    ) -> ConversationSnapshot | None:
        async with self._sessions() as session, session.begin():
            return await self._rebuild_snapshot_in_session(
                session, session_id, snapshot_ttl_seconds=snapshot_ttl_seconds
            )

    async def get_context(
        self,
        session_id: str,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
        max_tokens: int | None = None,
    ) -> ConversationContext:
        del max_tokens
        snapshot = await self.get_snapshot(session_id)
        source = "snapshot"
        if snapshot is None:
            snapshot = await self.rebuild_snapshot(session_id)
            source = "rebuilt"
        messages = await self.list_messages(session_id)
        messages = _bound_messages(messages, max_messages=max_messages, max_chars=max_chars)
        return ConversationContext(
            session_id=session_id,
            messages=messages,
            message_count=snapshot.message_count if snapshot else len(messages),
            source=source if snapshot else "cold",
            snapshot=snapshot,
        )

    async def delete_expired_snapshots(self, now: datetime) -> int:
        async with self._sessions() as session, session.begin():
            expires_at = col(ConversationSnapshotRow.expires_at)
            stmt = delete(ConversationSnapshotRow).where(
                expires_at.is_not(None),
                expires_at <= now,
            )
            result = await session.exec(stmt)
        return int(result.rowcount or 0)

    async def _rebuild_snapshot_in_session(
        self,
        session: AsyncSession,
        session_id: str,
        *,
        snapshot_ttl_seconds: int | None,
    ) -> ConversationSnapshot | None:
        session_row = await session.get(ConversationSessionRow, session_id)
        if session_row is None:
            return None
        rows = await self._message_rows(session, session_id, None)
        now = datetime.now(UTC)
        last = rows[-1] if rows else None
        expires_at = (
            now + timedelta(seconds=snapshot_ttl_seconds)
            if snapshot_ttl_seconds is not None
            else None
        )
        conversation_json = {"messages": [_message_json(row) for row in rows]}
        snapshot_row = await session.get(ConversationSnapshotRow, session_id)
        if snapshot_row is None:
            snapshot_row = ConversationSnapshotRow(
                session_id=session_id,
                user_id=session_row.user_id,
                conversation_json=conversation_json,
                message_count=len(rows),
                last_message_id=last.message_id if last else None,
                last_message_at=last.created_at if last else None,
                updated_at=now,
                expires_at=expires_at,
            )
            session.add(snapshot_row)
        else:
            snapshot_row.user_id = session_row.user_id
            snapshot_row.conversation_json = conversation_json
            snapshot_row.message_count = len(rows)
            snapshot_row.last_message_id = last.message_id if last else None
            snapshot_row.last_message_at = last.created_at if last else None
            snapshot_row.updated_at = now
            snapshot_row.expires_at = expires_at
        return _snapshot(snapshot_row)

    async def _message_rows(
        self, session: AsyncSession, session_id: str, limit: int | None
    ) -> list[ConversationMessageRow]:
        stmt = select(ConversationMessageRow).where(ConversationMessageRow.session_id == session_id)
        if limit is not None:
            recent = stmt.order_by(col(ConversationMessageRow.created_at).desc()).limit(limit)
            result = await session.exec(recent)
            return list(reversed(result.all()))
        result = await session.exec(stmt.order_by(col(ConversationMessageRow.created_at)))
        return list(result.all())


def _user(row: ConversationUserRow) -> User:
    return User(
        user_id=row.user_id,
        external_user_id=row.external_user_id,
        username=row.username,
        display_name=row.display_name,
        metadata=dict(row.metadata_json or {}),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _session(row: ConversationSessionRow) -> ConversationSession:
    return ConversationSession(
        session_id=row.session_id,
        user_id=row.user_id,
        system_name=row.system_name,
        config_path=row.config_path,
        title=row.title,
        metadata=dict(row.metadata_json or {}),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        last_message_at=_utc(row.last_message_at),
        expires_at=_utc(row.expires_at),
    )


def _message_row(message: ConversationMessage) -> ConversationMessageRow:
    return ConversationMessageRow(
        message_id=message.message_id,
        session_id=message.session_id,
        run_id=message.run_id,
        user_id=message.user_id,
        role=message.role.value,
        node_id=message.node_id,
        agent_id=message.agent_id,
        parent_message_id=message.parent_message_id,
        content=message.content,
        content_type=message.content_type,
        tool_name=message.tool_name,
        provider=message.provider,
        model_provider=message.model_provider,
        model_name=message.model_name,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        latency_ms=message.latency_ms,
        status=message.status,
        error_type=message.error_type,
        metadata_json=dict(message.metadata),
        created_at=message.created_at,
    )


def _message(row: ConversationMessageRow) -> ConversationMessage:
    return ConversationMessage(
        message_id=row.message_id,
        session_id=row.session_id,
        run_id=row.run_id,
        user_id=row.user_id,
        role=Role(row.role),
        node_id=row.node_id,
        agent_id=row.agent_id,
        parent_message_id=row.parent_message_id,
        content=row.content,
        content_type=row.content_type,
        tool_name=row.tool_name,
        provider=row.provider,
        model_provider=row.model_provider,
        model_name=row.model_name,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        status=row.status,
        error_type=row.error_type,
        metadata=dict(row.metadata_json or {}),
        created_at=_utc(row.created_at) or row.created_at,
    )


def _message_json(row: ConversationMessageRow) -> dict[str, Any]:
    return {
        "message_id": row.message_id,
        "run_id": row.run_id,
        "role": row.role,
        "content": row.content,
        "content_type": row.content_type,
        "node_id": row.node_id,
        "agent_id": row.agent_id,
        "tool_name": row.tool_name,
        "provider": row.provider,
        "status": row.status,
        "created_at": (_utc(row.created_at) or row.created_at).isoformat(),
        "metadata": dict(row.metadata_json or {}),
    }


def _snapshot(row: ConversationSnapshotRow) -> ConversationSnapshot:
    return ConversationSnapshot(
        session_id=row.session_id,
        user_id=row.user_id,
        conversation_json=dict(row.conversation_json or {}),
        message_count=row.message_count,
        last_message_id=row.last_message_id,
        last_message_at=_utc(row.last_message_at),
        model_context_tokens=row.model_context_tokens,
        updated_at=_utc(row.updated_at) or row.updated_at,
        expires_at=_utc(row.expires_at),
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


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
