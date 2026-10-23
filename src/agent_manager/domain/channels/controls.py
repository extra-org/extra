"""Durable control operations, with no model or organization data access."""

from abc import ABC, abstractmethod

from agent_manager.domain.channels.models import InboxItem


class ControlRepository(ABC):
    @abstractmethod
    async def suppressed(self, item: InboxItem) -> bool: ...

    @abstractmethod
    async def complete(
        self, item: InboxItem, reply: str | None, *, reset: bool = False
    ) -> None: ...
