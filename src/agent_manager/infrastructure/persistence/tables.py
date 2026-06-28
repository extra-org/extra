"""SQLModel tables — the single source of truth for the schema.

Dialect-portable: the same tables run on SQLite and PostgreSQL. Changes here are
captured as Alembic migrations, never created at application startup.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Index, Integer, Text, func
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


class ConversationUserRow(SQLModel, table=True):
    __tablename__ = "conversation_users"

    user_id: str = Field(primary_key=True, max_length=64)
    external_user_id: str | None = Field(default=None, index=True, max_length=256)
    username: str | None = Field(default=None, index=True, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    metadata_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class ConversationSessionRow(SQLModel, table=True):
    __tablename__ = "conversation_sessions"

    session_id: str = Field(primary_key=True, max_length=64)
    user_id: str | None = Field(default=None, foreign_key="conversation_users.user_id", index=True)
    system_name: str | None = Field(default=None, max_length=256)
    config_path: str | None = Field(default=None, max_length=1024)
    title: str | None = Field(default=None, max_length=512)
    metadata_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_message_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), index=True)
    )
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), index=True)
    )


class ConversationMessageRow(SQLModel, table=True):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index("idx_conversation_messages_session_created", "session_id", "created_at"),
        Index("idx_conversation_messages_session_message", "session_id", "message_id"),
    )

    message_id: str = Field(primary_key=True, max_length=64)
    session_id: str = Field(foreign_key="conversation_sessions.session_id", index=True)
    run_id: str | None = Field(default=None, index=True, max_length=64)
    user_id: str | None = Field(default=None, index=True, max_length=64)
    role: str = Field(max_length=32)
    node_id: str | None = Field(default=None, max_length=256)
    agent_id: str | None = Field(default=None, max_length=256)
    parent_message_id: str | None = Field(default=None, max_length=64)
    content: str = Field(sa_column=Column(Text, nullable=False))
    content_type: str = Field(default="text", max_length=64)
    tool_name: str | None = Field(default=None, max_length=256)
    provider: str | None = Field(default=None, max_length=64)
    model_provider: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=256)
    input_tokens: int | None = Field(default=None, sa_column=Column(Integer))
    output_tokens: int | None = Field(default=None, sa_column=Column(Integer))
    latency_ms: int | None = Field(default=None, sa_column=Column(Integer))
    status: str = Field(default="succeeded", max_length=64)
    error_type: str | None = Field(default=None, max_length=256)
    metadata_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class ConversationSnapshotRow(SQLModel, table=True):
    __tablename__ = "conversation_snapshots"

    session_id: str = Field(primary_key=True, foreign_key="conversation_sessions.session_id")
    user_id: str | None = Field(default=None, index=True, max_length=64)
    conversation_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    message_count: int = Field(default=0)
    last_message_id: str | None = Field(default=None, max_length=64)
    last_message_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    model_context_tokens: int | None = Field(default=None, sa_column=Column(Integer))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), index=True)
    )
