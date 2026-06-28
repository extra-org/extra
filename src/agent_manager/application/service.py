"""Application service — the use cases that give the stateless engine memory.

Depends only on ports: the engine (`agent_engine.Engine`) and the repository.
Both the engine transport and the database backend can change with no edits here.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from agent_engine.engine.engine import Engine
from agent_engine.engine.types import RunResult
from agent_engine.runtime.hooks import RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.application.context import build_prompt
from agent_manager.domain import ConversationMessage, Message, Repository, Role


class ConversationNotFound(Exception):
    """Raised when an operation targets a conversation id that does not exist."""


class ConversationService:
    def __init__(
        self,
        engine: Engine,
        repository: Repository,
        *,
        window: int = 10,
        max_chars: int | None = None,
        snapshot_ttl_seconds: int | None = 86_400,
        system_name: str | None = None,
        config_path: str | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._window = window
        self._max_chars = max_chars
        self._snapshot_ttl_seconds = snapshot_ttl_seconds
        self._system_name = system_name
        self._config_path = config_path

    async def create(self, *, user_id: str | None = None, session_id: str | None = None) -> str:
        if user_id:
            await self._repository.upsert_user(user_id)
        session = await self._repository.create_session(
            session_id,
            user_id=user_id,
            system_name=self._system_name,
            config_path=self._config_path,
        )
        return session.session_id

    async def history(self, conversation_id: str) -> list[Message]:
        await self._require(conversation_id)
        return await self._repository.list_messages(conversation_id)

    async def send(
        self, conversation_id: str, text: str, *, user_id: str | None = None
    ) -> RunResult:
        await self._require(conversation_id)
        if user_id:
            await self._repository.upsert_user(user_id)

        # Load prior history before saving the new message, or it gets inlined twice.
        prior_context = await self._repository.get_context(
            conversation_id, max_messages=self._window, max_chars=self._max_chars
        )
        run_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        await self._repository.append_message(
            ConversationMessage(
                message_id=uuid.uuid4().hex,
                session_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                role=Role.USER,
                content=text,
                created_at=now,
            ),
            snapshot_ttl_seconds=self._snapshot_ttl_seconds,
        )

        result = await self._engine.run(
            build_prompt(prior_context.messages, text, self._window),
            context=RunContext(run_id=run_id, conversation_id=conversation_id, user_id=user_id),
        )

        await self._repository.append_message(
            ConversationMessage(
                message_id=uuid.uuid4().hex,
                session_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                role=Role.ASSISTANT,
                content=result.answer,
                created_at=datetime.now(UTC),
                metadata={
                    "visited": list(result.visited),
                    "used_tools": [dataclasses.asdict(tool) for tool in result.used_tools],
                },
            ),
            snapshot_ttl_seconds=self._snapshot_ttl_seconds,
        )
        return result

    async def stream(
        self, conversation_id: str, text: str, *, user_id: str | None = None
    ) -> AsyncIterator[RunStreamEvent]:
        await self._require(conversation_id)
        if user_id:
            await self._repository.upsert_user(user_id)

        prior_context = await self._repository.get_context(
            conversation_id, max_messages=self._window, max_chars=self._max_chars
        )
        run_id = uuid.uuid4().hex
        await self._repository.append_message(
            ConversationMessage(
                message_id=uuid.uuid4().hex,
                session_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                role=Role.USER,
                content=text,
                created_at=datetime.now(UTC),
            ),
            snapshot_ttl_seconds=self._snapshot_ttl_seconds,
        )

        final: RunStreamEvent | None = None
        async for event in self._engine.stream(
            build_prompt(prior_context.messages, text, self._window),
            context=RunContext(run_id=run_id, conversation_id=conversation_id, user_id=user_id),
        ):
            if event.type == "final":
                final = event
            yield event

        if final is not None:
            await self._repository.append_message(
                ConversationMessage(
                    message_id=uuid.uuid4().hex,
                    session_id=conversation_id,
                    run_id=run_id,
                    user_id=user_id,
                    role=Role.ASSISTANT,
                    content=final.content or "",
                    created_at=datetime.now(UTC),
                    metadata={
                        "visited": list(final.route or ()),
                        "used_tools": [dataclasses.asdict(tool) for tool in final.used_tools],
                    },
                ),
                snapshot_ttl_seconds=self._snapshot_ttl_seconds,
            )

    async def _require(self, conversation_id: str) -> None:
        if not await self._repository.conversation_exists(conversation_id):
            raise ConversationNotFound(conversation_id)
