"""Authenticated webhook receipts and normalized delivery observations.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_webhook_receipts",
        sa.Column("receipt_id", sa.String(64), primary_key=True),
        sa.Column("connection_id", sa.String(128), nullable=False),
        sa.Column("system_id", sa.String(256), nullable=False),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_ids", sa.JSON(), nullable=False),
        sa.Column("delivery_event_ids", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.UniqueConstraint("connection_id", "body_digest"),
    )
    op.create_table(
        "channel_delivery_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("connection_id", sa.String(128), nullable=False),
        sa.Column("system_id", sa.String(256), nullable=False),
        sa.Column("provider_message_id", sa.String(512), nullable=False),
        sa.Column("recipient_id", sa.String(512), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_channel_delivery_message",
        "channel_delivery_events",
        ["connection_id", "provider_message_id"],
    )


def downgrade() -> None:
    op.drop_table("channel_delivery_events")
    op.drop_table("channel_webhook_receipts")
