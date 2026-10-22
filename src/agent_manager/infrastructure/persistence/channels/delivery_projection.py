"""Project authenticated observations by exact recipient and provider ID."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryBatchRow as Batch,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryEventRow as Event,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryPartRow as Part,
)


async def project(
    sessions: async_sessionmaker[AsyncSession], connection_id: str, system_id: str
) -> None:
    async with sessions() as session, session.begin():
        rows = (
            await session.exec(
                select(Event, Part, Batch)
                .join(Part, col(Part.provider_message_id) == col(Event.provider_message_id))
                .join(Batch, col(Batch.event_id) == col(Part.event_id))
                .where(
                    col(Event.connection_id) == connection_id,
                    col(Event.system_id) == system_id,
                    col(Batch.connection_id) == col(Event.connection_id),
                    col(Batch.system_id) == col(Event.system_id),
                    col(Batch.sender_id) == col(Event.recipient_id),
                    col(Event.projected).is_(False),
                )
                .order_by(col(Event.received_at), col(Event.event_id))
                .limit(100)
                .with_for_update()
            )
        ).all()
        touched: set[str] = set()
        for event, part, batch in rows:
            if event.status == "read":
                part.state = "read"
                part.reason = None
            elif event.status == "delivered" and part.state != "read":
                part.state = "delivered"
                part.reason = None
            elif event.status == "failed" and part.state not in {"delivered", "read"}:
                part.state = "failed"
                part.reason = "provider_delivery_failed"
            event.projected = True
            session.add(event)
            session.add(part)
            touched.add(batch.event_id)
        await session.flush()
        for event_id in touched:
            aggregate = await session.get(Batch, event_id)
            assert aggregate is not None
            parts = (await session.exec(select(Part).where(col(Part.event_id) == event_id))).all()
            states = {part.state for part in parts}
            aggregate.state = _batch_state(states)
            aggregate.reason = (
                "provider_delivery_failed"
                if aggregate.state == "failed"
                else aggregate.state
                if aggregate.state.startswith("blocked_")
                or aggregate.state in {"delivery_unknown", "suppressed"}
                else None
            )
            session.add(aggregate)


def _batch_state(states: set[str]) -> str:
    for blocking in (
        "delivery_unknown",
        "blocked_identity",
        "blocked_window",
        "suppressed",
        "failed",
        "sending",
        "retry",
        "ready",
    ):
        if blocking in states:
            return blocking
    if states == {"read"}:
        return "read"
    if states <= {"delivered", "read"}:
        return "delivered"
    return "accepted"
