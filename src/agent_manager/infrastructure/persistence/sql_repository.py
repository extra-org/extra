"""SQL-backed repository — one implementation, any SQL dialect.

SQLite and PostgreSQL are selected purely by the connection URL; there is no
per-database code here. The backend is a URL, not a branch.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain import Message, Repository, Role
from agent_manager.infrastructure.persistence.tables import ConversationRow, MessageRow


class SqlRepository(Repository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_conversation(self) -> str:
        cid = uuid.uuid4().hex
        async with self._sessions() as session, session.begin():
            session.add(ConversationRow(id=cid))
        return cid

    async def conversation_exists(self, conversation_id: str) -> bool:
        async with self._sessions() as session:
            return await session.get(ConversationRow, conversation_id) is not None

    async def add_message(self, conversation_id: str, role: Role, content: str) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                MessageRow(conversation_id=conversation_id, role=role.value, content=content)
            )

    async def list_messages(
        self, conversation_id: str, limit: int | None = None
    ) -> list[Message]:
        async with self._sessions() as session:
            stmt = select(MessageRow).where(MessageRow.conversation_id == conversation_id)
            if limit is not None:
                recent = stmt.order_by(col(MessageRow.id).desc()).limit(limit)
                rows = list(reversed((await session.exec(recent)).all()))
            else:
                rows = list((await session.exec(stmt.order_by(col(MessageRow.id).asc()))).all())
        return [
            Message(role=Role(r.role), content=r.content, created_at=r.created_at)
            for r in rows
        ]
