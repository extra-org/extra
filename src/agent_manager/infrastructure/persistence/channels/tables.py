"""Durable sender ownership, ordered inbox, and immutable reply parts."""

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class ChannelBindingRow(SQLModel, table=True):
    __tablename__ = "channel_bindings"

    connection_id: str = Field(primary_key=True, max_length=128)
    system_id: str = Field(primary_key=True, max_length=256)
    sender_id: str = Field(primary_key=True, max_length=512, repr=False)
    generation: int = 0
    user_id: str | None = Field(default=None, max_length=64)
    organization_id: str | None = Field(default=None, max_length=512)
    conversation_id: str | None = Field(default=None, max_length=64)
    active_event_id: str | None = Field(default=None, max_length=64)
    opted_out: bool = False
    suppressed_through_sequence: int = 0
    preference_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    latest_inbound_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class ChannelInboxRow(SQLModel, table=True):
    __tablename__ = "channel_inbox"
    __table_args__ = (
        UniqueConstraint("connection_id", "provider_message_id"),
        Index("idx_channel_inbox_pending", "connection_id", "system_id", "state", "sequence"),
    )

    sequence: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(unique=True, max_length=64)
    connection_id: str = Field(max_length=128)
    system_id: str = Field(max_length=256)
    sender_id: str = Field(max_length=512, repr=False)
    provider_message_id: str = Field(max_length=512)
    kind: str = Field(max_length=32)
    content: str = Field(sa_column=Column(Text, nullable=False), repr=False)
    received_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    state: str = Field(default="queued", max_length=32)
    claim_token: str | None = Field(default=None, max_length=64, repr=False)
    conversation_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    message_id: str | None = Field(default=None, max_length=64)
    response_id: str | None = Field(default=None, max_length=64)
    reset_generation: int | None = None


class ChannelOutboxRow(SQLModel, table=True):
    __tablename__ = "channel_outbox"
    __table_args__ = (UniqueConstraint("event_id", "response_id", "part_index"),)

    reply_id: str = Field(primary_key=True, max_length=64)
    event_id: str = Field(foreign_key="channel_inbox.event_id", index=True, max_length=64)
    response_id: str = Field(max_length=64)
    part_index: int
    text: str = Field(sa_column=Column(Text, nullable=False), repr=False)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class ChannelWebhookReceiptRow(SQLModel, table=True):
    __tablename__ = "channel_webhook_receipts"
    __table_args__ = (UniqueConstraint("connection_id", "body_digest"),)

    receipt_id: str = Field(primary_key=True, max_length=64)
    connection_id: str = Field(max_length=128)
    system_id: str = Field(max_length=256)
    body_digest: str = Field(max_length=64)
    received_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    event_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    delivery_event_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    issues: list[dict[str, str]] = Field(sa_column=Column(JSON, nullable=False))


class ChannelDeliveryEventRow(SQLModel, table=True):
    __tablename__ = "channel_delivery_events"
    __table_args__ = (
        Index("idx_channel_delivery_message", "connection_id", "provider_message_id"),
    )

    event_id: str = Field(primary_key=True, max_length=64)
    connection_id: str = Field(max_length=128)
    system_id: str = Field(max_length=256)
    provider_message_id: str = Field(max_length=512)
    recipient_id: str = Field(max_length=512, repr=False)
    status: str = Field(max_length=64)
    observed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    received_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    projected: bool = False


class ChannelDeliveryBatchRow(SQLModel, table=True):
    __tablename__ = "channel_delivery_batches"
    __table_args__ = (
        Index("idx_delivery_recipient", "connection_id", "system_id", "sender_id", "sequence"),
    )

    event_id: str = Field(primary_key=True, foreign_key="channel_inbox.event_id", max_length=64)
    connection_id: str = Field(max_length=128)
    system_id: str = Field(max_length=256)
    sender_id: str = Field(max_length=512, repr=False)
    sequence: int
    user_id: str | None = Field(default=None, max_length=64)
    organization_id: str | None = Field(default=None, max_length=512)
    state: str = Field(default="ready", max_length=32)
    reason: str | None = Field(default=None, max_length=64)


class ChannelDeliveryPartRow(SQLModel, table=True):
    __tablename__ = "channel_delivery_parts"
    __table_args__ = (
        UniqueConstraint("event_id", "part_index"),
        UniqueConstraint("connection_id", "provider_message_id"),
        Index("idx_delivery_pending", "state", "next_attempt_at"),
    )

    delivery_id: str = Field(primary_key=True, max_length=64)
    event_id: str = Field(foreign_key="channel_delivery_batches.event_id", max_length=64)
    connection_id: str = Field(max_length=128)
    part_index: int
    text: str = Field(sa_column=Column(Text, nullable=False), repr=False)
    state: str = Field(default="ready", max_length=32)
    attempts: int = 0
    token: str | None = Field(default=None, max_length=64, repr=False)
    provider_message_id: str | None = Field(default=None, max_length=512)
    next_attempt_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    reason: str | None = Field(default=None, max_length=64)
