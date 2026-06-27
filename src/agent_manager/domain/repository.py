"""The persistence port. Adapters implement it; the application depends on it."""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_manager.domain.models import Message, Role


class Repository(ABC):
    @abstractmethod
    async def create_conversation(self) -> str: ...

    @abstractmethod
    async def conversation_exists(self, conversation_id: str) -> bool: ...

    @abstractmethod
    async def add_message(self, conversation_id: str, role: Role, content: str) -> None: ...

    @abstractmethod
    async def list_messages(
        self, conversation_id: str, limit: int | None = None
    ) -> list[Message]:
        """Messages oldest-first. With `limit`, the most recent `limit`, still
        oldest-first."""
