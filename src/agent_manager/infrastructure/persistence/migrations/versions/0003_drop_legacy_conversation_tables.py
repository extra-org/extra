"""drop legacy conversations + messages tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-01

The `conversations`/`messages` tables (migration 0001) were superseded by
`conversation_sessions`/`conversation_messages`/`conversation_snapshots`
(migration 0002). Confirmed before writing this migration that nothing reads
from these tables anywhere in the codebase (no SELECT targets `ConversationRow`
or `MessageRow`): `SqlRepository` only ever wrote to them, from two
backward-compatibility overrides (`create_conversation`, `add_message`) that
no runtime caller used — `ConversationService` (the only production caller of
the repository, used by the CLI and the `agent_manager` API) exclusively uses
`create_session`/`append_message`. The only callers of the compat methods were
the repository contract tests, which exercise the current schema through the
base `Repository` alias implementations and do not depend on these tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_messages_conv", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")


def downgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_messages_conv", "messages", ["conversation_id", "id"])
