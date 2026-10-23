"""Provider-neutral delivery evidence; uncertain submission is never a retry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from agent_manager.domain.channels.models import ChannelAddress

SendKind = Literal["accepted", "retry", "failed", "unknown"]


@dataclass(frozen=True)
class SendOutcome:
    kind: SendKind
    provider_message_id: str | None = None
    retry_after: float = 0


class ChannelSender(ABC):
    @abstractmethod
    async def send(self, recipient: str, text: str) -> SendOutcome: ...


@dataclass(frozen=True)
class ReplySource:
    event_id: str
    texts: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class DeliveryPart:
    delivery_id: str
    event_id: str
    address: ChannelAddress
    user_id: str | None
    organization_id: str | None
    part_index: int
    text: str = field(repr=False)
    state: str
    attempts: int
    token: str | None = field(default=None, repr=False)
    provider_message_id: str | None = None
    next_attempt_at: datetime | None = None
    reason: str | None = None


class DeliveryRepository(ABC):
    @abstractmethod
    async def sources(self, connection_id: str, system_id: str) -> tuple[ReplySource, ...]: ...

    @abstractmethod
    async def materialize(self, event_id: str, parts: tuple[str, ...]) -> None: ...

    @abstractmethod
    async def recover_sending(self, connection_id: str, system_id: str) -> None: ...

    @abstractmethod
    async def claim_next(
        self, connection_id: str, system_id: str, now: datetime
    ) -> DeliveryPart | None: ...

    @abstractmethod
    async def guard(self, part: DeliveryPart, now: datetime) -> str | None: ...

    @abstractmethod
    async def complete(
        self,
        part: DeliveryPart,
        state: str,
        *,
        provider_id: str | None = None,
        next_attempt_at: datetime | None = None,
        reason: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def project(self, connection_id: str, system_id: str) -> None: ...

    @abstractmethod
    async def parts(self, event_id: str) -> tuple[DeliveryPart, ...]: ...
