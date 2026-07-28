"""add feedback column to conversation_messages

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages", sa.Column("feedback", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "feedback")
