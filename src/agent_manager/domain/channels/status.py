"""Operational counts for one connection, without message content or identifiers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChannelStatus:
    inbox_queued: int
    inbox_running: int
    inbox_answered: int
    inbox_needs_review: int
    inbox_failed: int
    # Seconds since the oldest unfinished inbound message was received.
    inbox_oldest_seconds: float | None
    # Unsent reply parts; one reply can be split into several parts.
    outbox_pending: int
    # Seconds since the inbound message behind the oldest unsent part arrived.
    outbox_oldest_seconds: float | None
    # Replies, not parts, whose delivery ended in each outcome.
    delivery_unknown: int
    delivery_failed: int
    delivery_blocked: int


class ChannelStatusReader(ABC):
    @abstractmethod
    async def read(self, connection_id: str, system_id: str, now: datetime) -> ChannelStatus: ...
