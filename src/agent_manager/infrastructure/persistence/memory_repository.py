"""In-memory repository. For tests and a second concrete proof of the port."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from agent_manager.domain import Message, Repository, Role


class MemoryRepository(Repository):
    def __init__(self) -> None:
        self._messages: dict[str, list[Message]] = {}

    async def create_conversation(self) -> str:
        cid = uuid.uuid4().hex
        self._messages[cid] = []
        return cid

    async def conversation_exists(self, conversation_id: str) -> bool:
        return conversation_id in self._messages

    async def add_message(self, conversation_id: str, role: Role, content: str) -> None:
        self._messages[conversation_id].append(
            Message(role=role, content=content, created_at=datetime.now(UTC))
        )

    async def list_messages(
        self, conversation_id: str, limit: int | None = None
    ) -> list[Message]:
        msgs = self._messages.get(conversation_id, [])
        return msgs[-limit:] if limit is not None else list(msgs)
