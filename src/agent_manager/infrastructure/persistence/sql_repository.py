"""SQL-backed conversation repository.

SQLite and PostgreSQL are selected by SQLAlchemy URL/driver. The application
layer depends only on the repository port, so backend details stay here.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, literal, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import aliased
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

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

    async def link_anonymous_user(self, anonymous_user_id: str, user_id: str) -> int:
        async with self._sessions() as session, session.begin():
            # Claiming the visitor is a conditional update, not a read then a
            # write: two tabs signing in at once would both pass a read check
            # and hand the same conversations to two accounts.
            claimed = await session.exec(
                update(ConversationUserRow)
                .where(
                    col(ConversationUserRow.user_id) == anonymous_user_id,
                    col(ConversationUserRow.linked_to_user_id).is_(None),
                )
                .values(linked_to_user_id=user_id, updated_at=datetime.now(UTC))
            )
            if _rows_affected(claimed) == 0:
                return 0
            moved = await session.exec(
                update(ConversationSessionRow)
                .where(col(ConversationSessionRow.user_id) == anonymous_user_id)
                .values(user_id=user_id)
            )
            return _rows_affected(moved)

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
            if row is not None:
                return _session(row)
            created = ConversationSessionRow(
                session_id=sid,
                user_id=user_id,
                system_name=system_name,
                config_path=config_path,
                title=title,
                head_message_id=None,
                metadata_json=dict(metadata or {}),
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            try:
                # A savepoint, so losing the id to a concurrent creator rolls
                # back only this insert and leaves the transaction usable.
                async with session.begin_nested():
                    session.add(created)
                row = created
            except IntegrityError:
                # Absorbed only if the id is now taken — then that caller won
                # and their row is the answer. Any other integrity failure
                # leaves nothing to find and stays the caller's.
                row = await session.get(ConversationSessionRow, sid)
                if row is None:
                    raise
        return _session(row)

    async def get_session(self, session_id: str) -> ConversationSession | None:
        async with self._sessions() as session:
            row = await session.get(ConversationSessionRow, session_id)
        return _session(row) if row else None

    async def list_sessions(
        self, user_id: str, page: PageRequest | None = None
    ) -> Page[ConversationSession]:
        page = page or PageRequest()
        limit = page.limit
        cursor = page.cursor
        sort_key = func.coalesce(
            ConversationSessionRow.last_message_at, ConversationSessionRow.created_at
        )
        conditions: list[Any] = [ConversationSessionRow.user_id == user_id]

        if cursor is not None:
            cursor_t, cursor_id = decode_cursor(cursor)
            conditions.append(
                or_(
                    sort_key < cursor_t,
                    and_(
                        sort_key == cursor_t,
                        col(ConversationSessionRow.session_id) < cursor_id,
                    ),
                )
            )

        stmt = (
            select(ConversationSessionRow)
            .where(*conditions)
            .order_by(
                sort_key.desc(),
                col(ConversationSessionRow.session_id).desc(),
            )
            .limit(limit + 1)
        )
        async with self._sessions() as session:
            rows = list((await session.exec(stmt)).all())

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = (
            encode_cursor(
                ensure_utc(rows[-1].last_message_at or rows[-1].created_at),  # type: ignore[arg-type]
                rows[-1].session_id,
            )
            if has_more and rows
            else None
        )

        return Page(
            items=[_session(row) for row in rows],
            next_cursor=next_cursor,
        )

    async def rename_session(self, session_id: str, title: str) -> None:
        async with self._sessions() as session:
            row = await session.get(ConversationSessionRow, session_id)
            if row is not None:
                row.title = title
                session.add(row)
                await session.commit()

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
                    head_message_id=message.message_id,
                    created_at=message.created_at,
                    updated_at=message.created_at,
                    last_message_at=message.created_at,
                    metadata_json={},
                )
                session.add(session_row)
            else:
                # No user_id here: writing a message never claims a conversation.
                if message.parent_message_id is None and session_row.head_message_id is not None:
                    message = replace(message, parent_message_id=session_row.head_message_id)
                session_row.updated_at = message.created_at
                session_row.last_message_at = message.created_at
                session_row.head_message_id = message.message_id

            session.add(_message_row(message))
            await session.flush()
            snapshot = await self._rebuild_snapshot_in_session(
                session, message.session_id, snapshot_ttl_seconds=snapshot_ttl_seconds
            )
            assert snapshot is not None
            return snapshot

    async def append_message_if_absent(
        self,
        message: ConversationMessage,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> bool:
        """Append once, retaining stale-branch children without selecting them."""
        try:
            async with self._sessions() as session, session.begin():
                if await session.get(ConversationMessageRow, message.message_id) is not None:
                    return False
                session_row = await session.get(ConversationSessionRow, message.session_id)
                if session_row is None:
                    session_row = ConversationSessionRow(
                        session_id=message.session_id,
                        user_id=message.user_id,
                        head_message_id=message.message_id,
                        created_at=message.created_at,
                        updated_at=message.created_at,
                        last_message_at=message.created_at,
                        metadata_json={},
                    )
                    session.add(session_row)
                    activate = True
                else:
                    activate = session_row.head_message_id == message.parent_message_id
                    if activate:
                        session_row.head_message_id = message.message_id
                        session_row.updated_at = message.created_at
                        session_row.last_message_at = message.created_at
                async with session.begin_nested():
                    session.add(_message_row(message))
                if activate:
                    await self._rebuild_snapshot_in_session(
                        session,
                        message.session_id,
                        snapshot_ttl_seconds=snapshot_ttl_seconds,
                    )
        except IntegrityError:
            async with self._sessions() as session:
                existing = await session.get(ConversationMessageRow, message.message_id)
            if existing is None:
                raise
            return False
        return True

    async def append_message_if_head(
        self,
        message: ConversationMessage,
        expected_head_message_id: str | None,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> bool:
        async with self._sessions() as session, session.begin():
            head = col(ConversationSessionRow.head_message_id)
            expected = (
                head.is_(None)
                if expected_head_message_id is None
                else head == expected_head_message_id
            )
            changed = await session.exec(
                update(ConversationSessionRow)
                .where(
                    col(ConversationSessionRow.session_id) == message.session_id,
                    expected,
                )
                .values(
                    head_message_id=message.message_id,
                    updated_at=message.created_at,
                    last_message_at=message.created_at,
                )
            )
            if _rows_affected(changed) == 0:
                return False
            session.add(_message_row(message))
            await session.flush()
            await self._rebuild_snapshot_in_session(
                session,
                message.session_id,
                snapshot_ttl_seconds=snapshot_ttl_seconds,
            )
        return True

    async def get_message(self, message_id: str) -> ConversationMessage | None:
        async with self._sessions() as session:
            row = await session.get(ConversationMessageRow, message_id)
        return _message(row) if row is not None else None

    async def get_user_message_for_run(
        self, session_id: str, run_id: str
    ) -> ConversationMessage | None:
        async with self._sessions() as session:
            result = await session.exec(
                select(ConversationMessageRow).where(
                    col(ConversationMessageRow.session_id) == session_id,
                    col(ConversationMessageRow.run_id) == run_id,
                    col(ConversationMessageRow.role) == Role.USER.value,
                )
            )
            row = result.first()
        return _message(row) if row is not None else None

    async def list_conversation_messages(
        self, session_id: str, limit: int | None = None
    ) -> list[ConversationMessage]:
        async with self._sessions() as session:
            rows = await self._active_message_rows(session, session_id, limit)
        return [_message(row) for row in rows]

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        rows = await self.list_conversation_messages(conversation_id, limit)
        return [
            Message(role=row.role, content=row.content, created_at=row.created_at) for row in rows
        ]

    async def update_message_feedback(
        self,
        message_id: str,
        feedback: str,
    ) -> ConversationMessage | None:
        async with self._sessions() as session:
            row = await session.get(ConversationMessageRow, message_id)
            if row is None:
                return None
            metadata = dict(row.metadata_json or {})
            metadata["feedback"] = feedback
            row.metadata_json = metadata
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _message(row)

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

    async def get_token_usage(self, conversation_id: str) -> int:
        async with self._sessions() as session:
            rows = await self._message_rows(session, conversation_id, None)
        return sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in rows)

    async def get_context(
        self,
        session_id: str,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
    ) -> ConversationContext:
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

    async def get_context_at(
        self,
        session_id: str,
        head_message_id: str | None,
        *,
        max_messages: int | None = None,
        max_chars: int | None = None,
    ) -> ConversationContext:
        async with self._sessions() as session:
            branch = await self._branch_message_rows(session, session_id, head_message_id)
        messages = [
            Message(role=Role(row.role), content=row.content, created_at=row.created_at)
            for row in branch
        ]
        bounded = _bound_messages(messages, max_messages=max_messages, max_chars=max_chars)
        return ConversationContext(
            session_id=session_id,
            messages=bounded,
            message_count=len(branch),
            source="branch",
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
        rows = await self._active_message_rows(session, session_id)
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
            recent = stmt.order_by(
                col(ConversationMessageRow.created_at).desc(),
                col(ConversationMessageRow.message_id).desc(),
            ).limit(limit)
            result = await session.exec(recent)
            return list(reversed(result.all()))
        result = await session.exec(
            stmt.order_by(
                col(ConversationMessageRow.created_at),
                col(ConversationMessageRow.message_id),
            )
        )
        return list(result.all())

    async def _active_message_rows(
        self, session: AsyncSession, session_id: str, limit: int | None = None
    ) -> list[ConversationMessageRow]:
        session_row = await session.get(ConversationSessionRow, session_id)
        if session_row is None:
            return []
        return await self._branch_message_rows(
            session, session_id, session_row.head_message_id, limit
        )

    async def _branch_message_rows(
        self,
        session: AsyncSession,
        session_id: str,
        head_message_id: str | None,
        limit: int | None = None,
    ) -> list[ConversationMessageRow]:
        """Return one branch, oldest first, walking `parent_message_id` in SQL.

        A recursive CTE keeps both the traversal and the limit in the database:
        history reads the tail of the active branch, never every message the
        conversation has stored.
        """
        if head_message_id is None or (limit is not None and limit <= 0):
            return []
        head = (
            select(ConversationMessageRow, literal(0).label("depth"))
            .where(
                col(ConversationMessageRow.session_id) == session_id,
                col(ConversationMessageRow.message_id) == head_message_id,
            )
            .cte("branch", recursive=True)
        )
        parent = aliased(ConversationMessageRow, name="parent_message")
        ancestors = select(parent, (head.c.depth + 1).label("depth")).where(
            col(parent.session_id) == session_id,
            col(parent.message_id) == head.c.parent_message_id,
        )
        if limit is not None:
            ancestors = ancestors.where(head.c.depth < limit - 1)
        branch = head.union_all(ancestors)
        result = await session.exec(
            select(ConversationMessageRow)
            .join(branch, col(ConversationMessageRow.message_id) == branch.c.message_id)
            .order_by(branch.c.depth.desc())
        )
        return list(result.all())


def _rows_affected(result: Any) -> int:
    """SQLAlchemy types `execute` as a generic `Result`; an UPDATE returns a
    `CursorResult`, which is what carries `rowcount`."""
    return int(result.rowcount)


def _user(row: ConversationUserRow) -> User:
    return User(
        user_id=row.user_id,
        external_user_id=row.external_user_id,
        username=row.username,
        display_name=row.display_name,
        linked_to_user_id=row.linked_to_user_id,
        metadata=dict(row.metadata_json or {}),
        created_at=ensure_utc(row.created_at),
        updated_at=ensure_utc(row.updated_at),
    )


def _session(row: ConversationSessionRow) -> ConversationSession:
    return ConversationSession(
        session_id=row.session_id,
        user_id=row.user_id,
        system_name=row.system_name,
        config_path=row.config_path,
        title=row.title,
        head_message_id=row.head_message_id,
        metadata=dict(row.metadata_json or {}),
        created_at=ensure_utc(row.created_at),
        updated_at=ensure_utc(row.updated_at),
        last_message_at=ensure_utc(row.last_message_at),
        expires_at=ensure_utc(row.expires_at),
    )


def _message_row(message: ConversationMessage) -> ConversationMessageRow:
    metadata = dict(message.metadata)
    if message.feedback is not None:
        metadata["feedback"] = message.feedback
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
        metadata_json=metadata,
        created_at=message.created_at,
    )


def _message(row: ConversationMessageRow) -> ConversationMessage:
    metadata = dict(row.metadata_json or {})
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
        metadata=metadata,
        feedback=metadata.get("feedback"),
        created_at=ensure_utc(row.created_at) or row.created_at,
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
        "created_at": (ensure_utc(row.created_at) or row.created_at).isoformat(),
        "metadata": dict(row.metadata_json or {}),
        "feedback": (row.metadata_json or {}).get("feedback"),
    }


def _snapshot(row: ConversationSnapshotRow) -> ConversationSnapshot:
    return ConversationSnapshot(
        session_id=row.session_id,
        user_id=row.user_id,
        conversation_json=dict(row.conversation_json or {}),
        message_count=row.message_count,
        last_message_id=row.last_message_id,
        last_message_at=ensure_utc(row.last_message_at),
        model_context_tokens=row.model_context_tokens,
        updated_at=ensure_utc(row.updated_at) or row.updated_at,
        expires_at=ensure_utc(row.expires_at),
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
