"""Durable normalized channel inbox, sender bindings, and reply parts.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_bindings",
        sa.Column("connection_id", sa.String(128), nullable=False),
        sa.Column("system_id", sa.String(256), nullable=False),
        sa.Column("sender_id", sa.String(512), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(64)),
        sa.Column("organization_id", sa.String(512)),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("active_event_id", sa.String(64)),
        sa.PrimaryKeyConstraint("connection_id", "system_id", "sender_id"),
    )
    op.create_table(
        "channel_inbox",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("connection_id", sa.String(128), nullable=False),
        sa.Column("system_id", sa.String(256), nullable=False),
        sa.Column("sender_id", sa.String(512), nullable=False),
        sa.Column("provider_message_id", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("claim_token", sa.String(64)),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("run_id", sa.String(64)),
        sa.Column("message_id", sa.String(64)),
        sa.Column("response_id", sa.String(64)),
        sa.Column("reset_generation", sa.Integer()),
        sa.UniqueConstraint("connection_id", "provider_message_id"),
    )
    op.create_index(
        "idx_channel_inbox_pending",
        "channel_inbox",
        ["connection_id", "system_id", "state", "sequence"],
    )
    op.create_table(
        "channel_outbox",
        sa.Column("reply_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("response_id", sa.String(64), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["channel_inbox.event_id"]),
        sa.UniqueConstraint("event_id", "response_id", "part_index"),
    )
    op.create_index("ix_channel_outbox_event_id", "channel_outbox", ["event_id"])


def downgrade() -> None:
    op.drop_table("channel_outbox")
    op.drop_table("channel_inbox")
    op.drop_table("channel_bindings")
