"""Commit control completion, optional static reply and reset in one transaction."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.controls import ControlRepository
from agent_manager.domain.channels.models import (
    ChannelClaimConflict,
    InboxItem,
    bounded_text,
    stable_id,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelInboxRow,
    ChannelOutboxRow,
)


class SqlControlRepository(ControlRepository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def suppressed(self, item: InboxItem) -> bool:
        address = item.message.address
        async with self._sessions() as session:
            binding = await session.get(
                ChannelBindingRow, (address.connection_id, address.system_id, address.sender_id)
            )
            return (
                binding is None
                or binding.opted_out
                or item.sequence <= binding.suppressed_through_sequence
            )

    async def complete(self, item: InboxItem, reply: str | None, *, reset: bool = False) -> None:
        if reply is not None:
            bounded_text(reply)
            if not reply.strip():
                raise ValueError("control reply must not be empty")
        async with self._sessions() as session, session.begin():
            row = (
                await session.exec(
                    select(ChannelInboxRow)
                    .where(col(ChannelInboxRow.event_id) == item.event_id)
                    .with_for_update()
                )
            ).first()
            if row is None or not item.claim_token or row.claim_token != item.claim_token:
                raise ChannelClaimConflict("control requires its durable claim")
            if row.state in {"answered", "done"}:
                return
            if row.state != "preparing" or (reset and row.kind != "reset"):
                raise ChannelClaimConflict("control is not preparing")
            binding = await session.get(
                ChannelBindingRow, (row.connection_id, row.system_id, row.sender_id)
            )
            if binding is None or binding.active_event_id != item.event_id:
                await session.refresh(row)
                if row.state in {"answered", "done"}:
                    return
                raise ChannelClaimConflict("control does not own its sender")
            response_id = (
                stable_id("control-response", item.event_id) if reply is not None else None
            )
            changed = await session.exec(
                update(ChannelInboxRow)
                .where(
                    col(ChannelInboxRow.event_id) == item.event_id,
                    col(ChannelInboxRow.claim_token) == item.claim_token,
                    col(ChannelInboxRow.state) == "preparing",
                )
                .values(
                    state="answered" if reply is not None else "done",
                    response_id=response_id,
                    reset_generation=binding.generation + 1 if reset else None,
                )
                .returning(col(ChannelInboxRow.event_id))
            )
            if changed.scalar_one_or_none() is None:
                await session.refresh(row)
                if row.state in {"answered", "done"}:
                    return
                raise ChannelClaimConflict("control changed before completion")
            values: dict[str, object] = {"active_event_id": None}
            if reset:
                values.update(generation=binding.generation + 1, conversation_id=None)
            released = await session.exec(
                update(ChannelBindingRow)
                .where(
                    col(ChannelBindingRow.connection_id) == row.connection_id,
                    col(ChannelBindingRow.system_id) == row.system_id,
                    col(ChannelBindingRow.sender_id) == row.sender_id,
                    col(ChannelBindingRow.active_event_id) == item.event_id,
                    col(ChannelBindingRow.generation) == binding.generation,
                )
                .values(**values)
                .returning(col(ChannelBindingRow.connection_id))
            )
            if released.scalar_one_or_none() is None:
                raise ChannelClaimConflict("control lost its sender generation")
            if reply is not None:
                assert response_id is not None
                session.add(
                    ChannelOutboxRow(
                        reply_id=stable_id("reply", item.event_id, response_id, "0"),
                        event_id=item.event_id,
                        response_id=response_id,
                        part_index=0,
                        text=reply,
                        created_at=datetime.now(UTC),
                    )
                )
