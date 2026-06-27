"""SQLModel tables — the single source of truth for the schema.

Dialect-portable: the same tables run on SQLite and PostgreSQL. Changes here are
captured as Alembic migrations, never created at application startup.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Text, func
from sqlmodel import Field, SQLModel


class ConversationRow(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(primary_key=True, max_length=64)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class MessageRow(SQLModel, table=True):
    __tablename__ = "messages"

    # Integer identity = stable, cross-dialect ordering key (SQLite's rowid has
    # no Postgres equivalent).
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True, max_length=64)
    role: str = Field(max_length=16)
    content: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
