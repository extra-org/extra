"""Bounded application-owned worker configuration, independent of provider payloads."""

from dataclasses import dataclass

from agent_manager.domain.channels.models import bounded_identifier


@dataclass(frozen=True)
class ChannelWorkerOptions:
    connection_id: str
    system_id: str
    organization_id: str
    concurrency: int = 4
    poll_seconds: float = 0.5
    turn_seconds: float = 120
    drain_seconds: float = 15

    def __post_init__(self) -> None:
        bounded_identifier(self.connection_id, limit=128)
        bounded_identifier(self.system_id, limit=256)
        bounded_identifier(self.organization_id)
        if not 1 <= self.concurrency <= 16:
            raise ValueError("channel worker concurrency must be between 1 and 16")
        if not (
            0 < self.poll_seconds <= 60
            and 0 < self.turn_seconds <= 3600
            and 0 < self.drain_seconds <= 60
        ):
            raise ValueError("channel worker time bounds must be finite and positive")
