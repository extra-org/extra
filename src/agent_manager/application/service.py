"""Application service — the use cases that give the stateless engine memory.

Depends only on ports: the engine (`agent_engine.Engine`) and the repository.
Both the engine transport and the database backend can change with no edits here.
"""

from __future__ import annotations

from agent_engine.engine.engine import Engine
from agent_engine.engine.types import RunResult
from agent_manager.application.context import build_prompt
from agent_manager.domain import Message, Repository, Role


class ConversationNotFound(Exception):
    """Raised when an operation targets a conversation id that does not exist."""


class ConversationService:
    def __init__(self, engine: Engine, repository: Repository, *, window: int = 10) -> None:
        self._engine = engine
        self._repository = repository
        self._window = window

    async def create(self) -> str:
        return await self._repository.create_conversation()

    async def history(self, conversation_id: str) -> list[Message]:
        await self._require(conversation_id)
        return await self._repository.list_messages(conversation_id)

    async def send(self, conversation_id: str, text: str) -> RunResult:
        await self._require(conversation_id)

        # Load prior history before saving the new message, or it gets inlined twice.
        prior = await self._repository.list_messages(conversation_id, limit=self._window)
        await self._repository.add_message(conversation_id, Role.USER, text)

        result = await self._engine.run(build_prompt(prior, text, self._window))

        await self._repository.add_message(conversation_id, Role.ASSISTANT, result.answer)
        return result

    async def _require(self, conversation_id: str) -> None:
        if not await self._repository.conversation_exists(conversation_id):
            raise ConversationNotFound(conversation_id)
