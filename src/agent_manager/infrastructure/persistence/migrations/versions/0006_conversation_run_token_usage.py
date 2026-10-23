"""retain lifetime conversation token usage independently of assistant messages

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    usage = op.create_table(
        "conversation_run_token_usage",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.session_id"]),
        sa.PrimaryKeyConstraint("session_id", "run_id"),
    )
    # One row per charged execution leg, so a retried leg write applies once.
    op.create_table(
        "agent_run_token_legs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("leg_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "leg_id"),
    )
    # Include pre-upgrade cancelled and pending runs, which have no answer row.
    # Some older installations predate the run table added to the baseline.
    if not sa.inspect(op.get_bind()).has_table("agent_runs"):
        return
    messages = sa.table(
        "conversation_messages",
        sa.column("session_id"),
        sa.column("run_id"),
    )
    runs = sa.table(
        "agent_runs", sa.column("run_id"), sa.column("input_tokens"), sa.column("output_tokens")
    )
    op.execute(
        usage.insert().from_select(
            ["session_id", "run_id", "total_tokens"],
            sa.select(
                messages.c.session_id,
                runs.c.run_id,
                sa.func.coalesce(runs.c.input_tokens, 0)
                + sa.func.coalesce(runs.c.output_tokens, 0),
            )
            .select_from(messages.join(runs, messages.c.run_id == runs.c.run_id))
            .distinct(),
        )
    )


def downgrade() -> None:
    op.drop_table("agent_run_token_legs")
    op.drop_table("conversation_run_token_usage")
