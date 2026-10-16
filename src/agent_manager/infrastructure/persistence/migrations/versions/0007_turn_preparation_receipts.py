"""Atomically bind preparation keys to their original user messages.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_turn_preparations",
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("key_digest"),
        sa.UniqueConstraint("message_id"),
    )


def downgrade() -> None:
    op.drop_table("conversation_turn_preparations")
