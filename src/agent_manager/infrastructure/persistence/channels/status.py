"""Grouped counts over channel queues; never selects text or sender identifiers."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.models import InboxState
from agent_manager.domain.channels.status import ChannelStatus, ChannelStatusReader
from agent_manager.domain.pagination import ensure_utc
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryBatchRow as Batch,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryPartRow as Part,
)
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelInboxRow as Inbox,
)

_RUNNING = (InboxState.PREPARING, InboxState.PREPARED, InboxState.RUNNING)
_UNFINISHED = (InboxState.QUEUED, *_RUNNING, InboxState.ANSWERED)
_UNSENT = ("ready", "retry", "sending")


class SqlChannelStatusReader(ChannelStatusReader):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def read(self, connection_id: str, system_id: str, now: datetime) -> ChannelStatus:
        async with self._sessions() as session:
            inbox = (
                await session.exec(
                    select(
                        Inbox.state,
                        func.count(),
                        func.min(Inbox.received_at),
                    )
                    .where(
                        col(Inbox.connection_id) == connection_id,
                        col(Inbox.system_id) == system_id,
                    )
                    .group_by(col(Inbox.state))
                )
            ).all()
            parts = (
                await session.exec(
                    select(
                        Part.state,
                        func.count(),
                        func.min(Inbox.received_at),
                    )
                    .join(
                        Batch,
                        col(Batch.event_id) == col(Part.event_id),
                    )
                    .join(
                        Inbox,
                        col(Inbox.event_id) == col(Batch.event_id),
                    )
                    .where(
                        col(Batch.connection_id) == connection_id,
                        col(Batch.system_id) == system_id,
                    )
                    .group_by(col(Part.state))
                )
            ).all()
            # Outcomes count replies: a reply mirrors its part's failure state.
            replies = (
                await session.exec(
                    select(Batch.state, func.count())
                    .where(
                        col(Batch.connection_id) == connection_id,
                        col(Batch.system_id) == system_id,
                    )
                    .group_by(col(Batch.state))
                )
            ).all()
        inbox_counts = {state: count for state, count, _ in inbox}
        part_counts = {state: count for state, count, _ in parts}
        reply_counts = dict(replies)
        return ChannelStatus(
            inbox_queued=inbox_counts.get(InboxState.QUEUED, 0),
            inbox_running=sum(inbox_counts.get(state, 0) for state in _RUNNING),
            inbox_answered=inbox_counts.get(InboxState.ANSWERED, 0),
            inbox_needs_review=inbox_counts.get(InboxState.NEEDS_REVIEW, 0),
            inbox_failed=inbox_counts.get(InboxState.FAILED, 0),
            inbox_oldest_seconds=_age(now, (o for s, _, o in inbox if s in _UNFINISHED)),
            outbox_pending=sum(part_counts.get(state, 0) for state in _UNSENT),
            outbox_oldest_seconds=_age(now, (o for s, _, o in parts if s in _UNSENT)),
            delivery_unknown=reply_counts.get("delivery_unknown", 0),
            delivery_failed=reply_counts.get("failed", 0),
            delivery_blocked=sum(
                count for state, count in reply_counts.items() if state.startswith("blocked_")
            ),
        )


def _age(now: datetime, oldest: Iterable[datetime | None]) -> float | None:
    times = [value for value in map(ensure_utc, oldest) if value is not None]
    return max(0.0, (now - min(times)).total_seconds()) if times else None
