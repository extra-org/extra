"""Shared inbox insert within a caller-owned transaction."""

from sqlalchemy import case, or_, update
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import Insert as SqliteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.models import (
    ChannelMessageConflict,
    InboundMessage,
    InboxState,
    stable_id,
)
from agent_manager.domain.pagination import ensure_utc
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelInboxRow,
)


def sql_insert(session: AsyncSession, model: type[SQLModel]) -> SqliteInsert | PostgresInsert:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return sqlite_insert(model)
    return postgres_insert(model)


async def ingest_in_session(session: AsyncSession, message: InboundMessage) -> ChannelInboxRow:
    address = message.address
    event_id = stable_id("inbox", address.connection_id, message.provider_message_id)
    inserted = await session.exec(
        sql_insert(session, ChannelInboxRow)
        .values(
            event_id=event_id,
            connection_id=address.connection_id,
            system_id=address.system_id,
            sender_id=address.sender_id,
            provider_message_id=message.provider_message_id,
            content=message.content,
            kind=message.kind,
            received_at=message.received_at,
            sent_at=message.sent_at,
            state=InboxState.QUEUED.value,
        )
        .on_conflict_do_nothing(index_elements=["connection_id", "provider_message_id"])
        .returning(col(ChannelInboxRow.event_id))
    )
    created = inserted.scalar_one_or_none() is not None
    rows = await session.exec(
        select(ChannelInboxRow).where(col(ChannelInboxRow.event_id) == event_id)
    )
    row = rows.one()
    if not created:
        if (
            row.system_id != address.system_id
            or row.sender_id != address.sender_id
            or row.kind != message.kind
            or row.content != message.content
            or ensure_utc(row.sent_at) != message.sent_at
        ):
            raise ChannelMessageConflict("provider message is bound to different input")
        return row
    await session.exec(
        sql_insert(session, ChannelBindingRow)
        .values(
            connection_id=address.connection_id,
            system_id=address.system_id,
            sender_id=address.sender_id,
            generation=0,
        )
        .on_conflict_do_nothing(index_elements=["connection_id", "system_id", "sender_id"])
    )
    await _inbound_policy(session, row, message)
    return row


async def _inbound_policy(
    session: AsyncSession, row: ChannelInboxRow, message: InboundMessage
) -> None:
    binding = ChannelBindingRow
    observed = min(message.sent_at, message.received_at) if message.sent_at else None
    values: dict[str, object] = {}
    if observed is not None:
        values["latest_inbound_at"] = case(
            (
                or_(
                    col(binding.latest_inbound_at).is_(None),
                    col(binding.latest_inbound_at) < observed,
                ),
                observed,
            ),
            else_=binding.latest_inbound_at,
        )
    if message.kind in {"stop", "start"}:
        preference = observed or message.received_at
        fresh = or_(col(binding.preference_at).is_(None), col(binding.preference_at) <= preference)
        cutoff = row.sequence if message.kind == "stop" else (row.sequence or 1) - 1
        values["suppressed_through_sequence"] = case(
            (col(binding.suppressed_through_sequence) < cutoff, cutoff),
            else_=binding.suppressed_through_sequence,
        )
        values["opted_out"] = (
            True if message.kind == "stop" else case((fresh, False), else_=binding.opted_out)
        )
        values["preference_at"] = case((fresh, preference), else_=binding.preference_at)
    if values:
        await session.exec(
            update(binding)
            .where(
                col(binding.connection_id) == row.connection_id,
                col(binding.system_id) == row.system_id,
                col(binding.sender_id) == row.sender_id,
            )
            .values(**values)
        )
