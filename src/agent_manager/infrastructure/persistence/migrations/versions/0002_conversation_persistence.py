"""conversation users, sessions, cold messages, and hot snapshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_users",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("external_user_id", sa.String(length=256), nullable=True),
        sa.Column("username", sa.String(length=256), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_conversation_users_external_user_id",
        "conversation_users",
        ["external_user_id"],
    )
    op.create_index("ix_conversation_users_username", "conversation_users", ["username"])

    op.create_table(
        "conversation_sessions",
        sa.Column("session_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("system_name", sa.String(length=256), nullable=True),
        sa.Column("config_path", sa.String(length=1024), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["conversation_users.user_id"]),
    )
    op.create_index("ix_conversation_sessions_user_id", "conversation_sessions", ["user_id"])
    op.create_index(
        "ix_conversation_sessions_last_message_at",
        "conversation_sessions",
        ["last_message_at"],
    )
    op.create_index("ix_conversation_sessions_expires_at", "conversation_sessions", ["expires_at"])

    op.create_table(
        "conversation_messages",
        sa.Column("message_id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=256), nullable=True),
        sa.Column("agent_id", sa.String(length=256), nullable=True),
        sa.Column("parent_message_id", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=256), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=256), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("error_type", sa.String(length=256), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.session_id"]),
    )
    op.create_index(
        "idx_conversation_messages_session_created",
        "conversation_messages",
        ["session_id", "created_at"],
    )
    op.create_index(
        "idx_conversation_messages_session_message",
        "conversation_messages",
        ["session_id", "message_id"],
    )
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_index("ix_conversation_messages_run_id", "conversation_messages", ["run_id"])
    op.create_index("ix_conversation_messages_user_id", "conversation_messages", ["user_id"])

    op.create_table(
        "conversation_snapshots",
        sa.Column("session_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("conversation_json", sa.JSON(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("last_message_id", sa.String(length=64), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_context_tokens", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.session_id"]),
    )
    op.create_index("ix_conversation_snapshots_user_id", "conversation_snapshots", ["user_id"])
    op.create_index(
        "ix_conversation_snapshots_expires_at",
        "conversation_snapshots",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_snapshots_expires_at", table_name="conversation_snapshots")
    op.drop_index("ix_conversation_snapshots_user_id", table_name="conversation_snapshots")
    op.drop_table("conversation_snapshots")

    op.drop_index("ix_conversation_messages_user_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_run_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_session_id", table_name="conversation_messages")
    op.drop_index("idx_conversation_messages_session_message", table_name="conversation_messages")
    op.drop_index("idx_conversation_messages_session_created", table_name="conversation_messages")
    op.drop_table("conversation_messages")

    op.drop_index("ix_conversation_sessions_expires_at", table_name="conversation_sessions")
    op.drop_index("ix_conversation_sessions_last_message_at", table_name="conversation_sessions")
    op.drop_index("ix_conversation_sessions_user_id", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")

    op.drop_index("ix_conversation_users_username", table_name="conversation_users")
    op.drop_index("ix_conversation_users_external_user_id", table_name="conversation_users")
    op.drop_table("conversation_users")
