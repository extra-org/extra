"""Durable provider rendering and CAS attempt claims, separate from answers."""

import secrets
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, or_, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import aliased
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.delivery import DeliveryPart, DeliveryRepository, ReplySource
from agent_manager.domain.channels.models import (
    ChannelAddress,
    ChannelClaimConflict,
    ChannelMessageConflict,
    bounded_identifier,
    stable_id,
)
from agent_manager.domain.pagination import ensure_utc
from agent_manager.infrastructure.persistence.channels.intake import sql_insert
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelInboxRow,
    ChannelOutboxRow,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryBatchRow as Batch,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryPartRow as Part,
)
from agent_manager.infrastructure.persistence.tables import ConversationSessionRow

ADVANCED = ("accepted", "delivered", "read", "suppressed")
POSITIVE = ("accepted", "delivered", "read")


class SqlDeliveryRepository(DeliveryRepository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def sources(self, connection_id: str, system_id: str) -> tuple[ReplySource, ...]:
        async with self._sessions() as session:
            rows = (
                await session.exec(
                    select(ChannelInboxRow)
                    .where(
                        col(ChannelInboxRow.connection_id) == connection_id,
                        col(ChannelInboxRow.system_id) == system_id,
                        col(ChannelInboxRow.state) == "answered",
                        ~exists().where(col(Batch.event_id) == col(ChannelInboxRow.event_id)),
                    )
                    .order_by(col(ChannelInboxRow.sequence))
                    .limit(32)
                )
            ).all()
            result = []
            for row in rows:
                texts = (
                    await session.exec(
                        select(ChannelOutboxRow)
                        .where(col(ChannelOutboxRow.event_id) == row.event_id)
                        .order_by(col(ChannelOutboxRow.part_index))
                        .limit(128)
                    )
                ).all()
                if texts:
                    result.append(ReplySource(row.event_id, tuple(part.text for part in texts)))
            return tuple(result)

    async def materialize(self, event_id: str, parts: tuple[str, ...]) -> None:
        if len(parts) > 128 or any(
            not text or len(text.encode("utf-16-le")) // 2 > 4096 or "\x00" in text
            for text in parts
        ):
            raise ValueError("invalid provider part plan")
        async with self._sessions() as session, session.begin():
            inbox = (
                await session.exec(
                    select(ChannelInboxRow).where(col(ChannelInboxRow.event_id) == event_id)
                )
            ).first()
            if inbox is None or inbox.state != "answered":
                raise ChannelClaimConflict("delivery source is not answered")
            binding = await session.get(
                ChannelBindingRow, (inbox.connection_id, inbox.system_id, inbox.sender_id)
            )
            owner = (
                await session.get(ConversationSessionRow, inbox.conversation_id)
                if inbox.conversation_id
                else None
            )
            state = "ready" if parts else "blocked_format"
            if inbox.kind not in {"reset", "help", "start", "unsupported"} and (
                owner is None
                or not owner.user_id
                or binding is None
                or binding.user_id != owner.user_id
                or not binding.organization_id
            ):
                state = "blocked_identity"
            inserted = await session.exec(
                sql_insert(session, Batch)
                .values(
                    event_id=event_id,
                    connection_id=inbox.connection_id,
                    system_id=inbox.system_id,
                    sender_id=inbox.sender_id,
                    sequence=inbox.sequence,
                    user_id=owner.user_id if owner else None,
                    organization_id=binding.organization_id if binding else None,
                    state=state,
                    reason=None if state == "ready" else state,
                )
                .on_conflict_do_nothing(index_elements=["event_id"])
                .returning(col(Batch.event_id))
            )
            if inserted.scalar_one_or_none() is None:
                existing = (
                    await session.exec(
                        select(Part)
                        .where(col(Part.event_id) == event_id)
                        .order_by(col(Part.part_index))
                    )
                ).all()
                if tuple(row.text for row in existing) != parts:
                    raise ChannelMessageConflict("provider part plan is immutable")
                return
            for index, text in enumerate(parts):
                session.add(
                    Part(
                        delivery_id=stable_id("delivery-part", event_id, str(index)),
                        event_id=event_id,
                        connection_id=inbox.connection_id,
                        part_index=index,
                        text=text,
                    )
                )

    async def recover_sending(self, connection_id: str, system_id: str) -> None:
        async with self._sessions() as session, session.begin():
            ids = select(Batch.event_id).where(
                col(Batch.connection_id) == connection_id,
                col(Batch.system_id) == system_id,
                col(Batch.state) == "sending",
            )
            await session.exec(
                update(Part)
                .where(col(Part.event_id).in_(ids), col(Part.state) == "sending")
                .values(state="delivery_unknown", reason="interrupted_send")
            )
            await session.exec(
                update(Batch)
                .where(col(Batch.event_id).in_(ids))
                .values(state="delivery_unknown", reason="interrupted_send")
            )

    async def claim_next(
        self, connection_id: str, system_id: str, now: datetime
    ) -> DeliveryPart | None:
        older = aliased(Batch)
        previous = aliased(Part)
        unplanned = aliased(ChannelInboxRow)
        planned = aliased(Batch)
        async with self._sessions() as session, session.begin():
            rows = (
                await session.exec(
                    select(Part, Batch)
                    .join(Batch, col(Batch.event_id) == col(Part.event_id))
                    .where(
                        col(Batch.connection_id) == connection_id,
                        col(Batch.system_id) == system_id,
                        col(Batch.state).in_(("ready", "retry")),
                        col(Part.state).in_(("ready", "retry")),
                        or_(col(Part.next_attempt_at).is_(None), col(Part.next_attempt_at) <= now),
                        ~exists().where(
                            col(previous.event_id) == col(Part.event_id),
                            col(previous.part_index) < col(Part.part_index),
                            col(previous.state).not_in(POSITIVE),
                        ),
                        ~exists().where(
                            and_(
                                col(older.connection_id) == col(Batch.connection_id),
                                col(older.system_id) == col(Batch.system_id),
                                col(older.sender_id) == col(Batch.sender_id),
                                col(older.sequence) < col(Batch.sequence),
                                col(older.state).not_in(ADVANCED),
                            )
                        ),
                        ~exists().where(
                            col(unplanned.connection_id) == col(Batch.connection_id),
                            col(unplanned.system_id) == col(Batch.system_id),
                            col(unplanned.sender_id) == col(Batch.sender_id),
                            col(unplanned.sequence) < col(Batch.sequence),
                            col(unplanned.state) == "answered",
                            ~exists()
                            .where(col(planned.event_id) == col(unplanned.event_id))
                            .correlate(unplanned),
                        ),
                    )
                    .order_by(col(Batch.sequence), col(Part.part_index))
                    .limit(32)
                )
            ).all()
            for part, batch in rows:
                token = secrets.token_hex(16)
                changed = await session.exec(
                    update(Part)
                    .where(
                        col(Part.delivery_id) == part.delivery_id,
                        col(Part.state) == part.state,
                        col(Part.attempts) == part.attempts,
                    )
                    .values(state="sending", attempts=part.attempts + 1, token=token)
                    .returning(Part)
                )
                claimed = changed.scalar_one_or_none()
                if claimed is not None:
                    batch.state = "sending"
                    session.add(batch)
                    return _part(claimed, batch)
            return None

    async def guard(self, part: DeliveryPart, now: datetime) -> str | None:
        async with self._sessions() as session:
            batch = await session.get(Batch, part.event_id)
            binding = await session.get(
                ChannelBindingRow,
                (part.address.connection_id, part.address.system_id, part.address.sender_id),
            )
            if batch is None or binding is None:
                return "blocked_identity"
            if binding.opted_out or batch.sequence <= binding.suppressed_through_sequence:
                return "suppressed"
            if part.user_id is not None and (
                binding.user_id != part.user_id or binding.organization_id != part.organization_id
            ):
                return "blocked_identity"
            latest = ensure_utc(binding.latest_inbound_at)
            if latest is None or latest > now or now >= latest + timedelta(hours=24):
                return "blocked_window"
            return None

    async def complete(
        self,
        part: DeliveryPart,
        state: str,
        *,
        provider_id: str | None = None,
        next_attempt_at: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        if state not in {
            "accepted",
            "retry",
            "failed",
            "delivery_unknown",
            "blocked_identity",
            "blocked_window",
            "suppressed",
        }:
            raise ValueError("invalid send completion state")
        if provider_id is not None:
            bounded_identifier(provider_id)
        if state == "accepted" and not provider_id:
            raise ValueError("acceptance requires a provider message ID")
        async with self._sessions() as session, session.begin():
            changed = await session.exec(
                update(Part)
                .where(
                    col(Part.delivery_id) == part.delivery_id,
                    col(Part.state) == "sending",
                    col(Part.token) == part.token,
                )
                .values(
                    state=state,
                    provider_message_id=provider_id,
                    next_attempt_at=next_attempt_at,
                    reason=reason,
                )
                .returning(col(Part.delivery_id))
            )
            if changed.scalar_one_or_none() is None:
                raise ChannelClaimConflict("send attempt is no longer owned")
            batch = await session.get(Batch, part.event_id)
            assert batch is not None
            pending = (
                await session.exec(
                    select(Part.delivery_id).where(
                        col(Part.event_id) == part.event_id, col(Part.state).not_in(POSITIVE)
                    )
                )
            ).first()
            batch.state = ("ready" if pending else "accepted") if state == "accepted" else state
            batch.reason = reason
            session.add(batch)
            if batch.state == "accepted":
                await session.exec(
                    update(ChannelInboxRow)
                    .where(
                        col(ChannelInboxRow.event_id) == part.event_id,
                        col(ChannelInboxRow.state) == "answered",
                    )
                    .values(state="done")
                )

    async def project(self, connection_id: str, system_id: str) -> None:
        from agent_manager.infrastructure.persistence.channels.delivery_projection import project

        await project(self._sessions, connection_id, system_id)

    async def parts(self, event_id: str) -> tuple[DeliveryPart, ...]:
        async with self._sessions() as session:
            rows = (
                await session.exec(
                    select(Part, Batch)
                    .join(Batch, col(Batch.event_id) == col(Part.event_id))
                    .where(col(Part.event_id) == event_id)
                    .order_by(col(Part.part_index))
                )
            ).all()
            return tuple(_part(part, batch) for part, batch in rows)


def _part(part: Part, batch: Batch) -> DeliveryPart:
    return DeliveryPart(
        part.delivery_id,
        part.event_id,
        ChannelAddress(batch.connection_id, batch.system_id, batch.sender_id),
        batch.user_id,
        batch.organization_id,
        part.part_index,
        part.text,
        part.state,
        part.attempts,
        part.token,
        part.provider_message_id,
        ensure_utc(part.next_attempt_at),
        part.reason,
    )
