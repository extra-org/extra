"""Durable channel handoff and ownership, separate from agent execution."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from agent_manager.domain.channels.models import (
    ChannelAddress,
    ChannelBinding,
    InboundMessage,
    InboxItem,
    InboxState,
    StagedReply,
)


class ChannelRepository(ABC):
    @abstractmethod
    async def ingest(self, message: InboundMessage) -> InboxItem: ...

    @abstractmethod
    async def get_inbound(self, event_id: str) -> InboxItem | None: ...

    @abstractmethod
    async def get_binding(self, address: ChannelAddress) -> ChannelBinding | None: ...

    @abstractmethod
    async def claim_next(self, connection_id: str, system_id: str) -> InboxItem | None: ...

    @abstractmethod
    async def list_active(
        self, connection_id: str, system_id: str, *, after_sequence: int = 0, limit: int = 32
    ) -> tuple[InboxItem, ...]:
        """Bounded recovery scan; only the exclusive supervisor may schedule these."""

    @abstractmethod
    async def bind_conversation(
        self, event_id: str, token: str, user_id: str, organization_id: str, conversation_id: str
    ) -> ChannelBinding: ...

    @abstractmethod
    async def record_prepared(
        self, event_id: str, token: str, conversation_id: str, run_id: str, message_id: str
    ) -> InboxItem: ...

    @abstractmethod
    async def claim_execution(self, event_id: str, token: str) -> bool: ...

    @abstractmethod
    async def finish(self, event_id: str, token: str, state: InboxState) -> None: ...

    @abstractmethod
    async def reset_conversation(self, event_id: str, token: str) -> int: ...

    @abstractmethod
    async def stage_reply(
        self, event_id: str, token: str, response_id: str, parts: Sequence[str]
    ) -> tuple[StagedReply, ...]: ...

    @abstractmethod
    async def list_replies(self, event_id: str) -> tuple[StagedReply, ...]: ...
