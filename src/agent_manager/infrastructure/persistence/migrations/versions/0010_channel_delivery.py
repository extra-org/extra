"""Provider delivery parts and durable sender suppression/window state.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_bindings",
        sa.Column("opted_out", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "channel_bindings",
        sa.Column("suppressed_through_sequence", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("channel_bindings", sa.Column("preference_at", sa.DateTime(timezone=True)))
    op.add_column("channel_bindings", sa.Column("latest_inbound_at", sa.DateTime(timezone=True)))
    op.add_column(
        "channel_delivery_events",
        sa.Column("projected", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        sa.text("""UPDATE channel_bindings SET latest_inbound_at = (
        SELECT MAX(CASE WHEN sent_at > received_at THEN received_at ELSE sent_at END)
        FROM channel_inbox WHERE channel_inbox.connection_id = channel_bindings.connection_id
        AND channel_inbox.system_id = channel_bindings.system_id
        AND channel_inbox.sender_id = channel_bindings.sender_id AND sent_at IS NOT NULL
        AND kind IN ('text', 'reset', 'help', 'stop', 'start')
    )""")
    )
    op.create_table(
        "channel_delivery_batches",
        sa.Column(
            "event_id", sa.String(64), sa.ForeignKey("channel_inbox.event_id"), primary_key=True
        ),
        sa.Column("connection_id", sa.String(128), nullable=False),
        sa.Column("system_id", sa.String(256), nullable=False),
        sa.Column("sender_id", sa.String(512), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(64)),
        sa.Column("organization_id", sa.String(512)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64)),
    )
    op.create_index(
        "idx_delivery_recipient",
        "channel_delivery_batches",
        ["connection_id", "system_id", "sender_id", "sequence"],
    )
    op.create_table(
        "channel_delivery_parts",
        sa.Column("delivery_id", sa.String(64), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(64),
            sa.ForeignKey("channel_delivery_batches.event_id"),
            nullable=False,
        ),
        sa.Column("connection_id", sa.String(128), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64)),
        sa.Column("provider_message_id", sa.String(512)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(64)),
        sa.UniqueConstraint("event_id", "part_index"),
        sa.UniqueConstraint("connection_id", "provider_message_id"),
    )
    op.create_index("idx_delivery_pending", "channel_delivery_parts", ["state", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("channel_delivery_parts")
    op.drop_table("channel_delivery_batches")
    op.drop_column("channel_delivery_events", "projected")
    for column in (
        "latest_inbound_at",
        "preference_at",
        "suppressed_through_sequence",
        "opted_out",
    ):
        op.drop_column("channel_bindings", column)
