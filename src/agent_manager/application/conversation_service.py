"""Application service — the use cases that give the stateless engine memory.

Depends only on ports: the engine (`agent_engine.Engine`) and the repository.
Both the engine transport and the database backend can change with no edits here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.errors import ApprovalAlreadyProcessed, RunNotFound
from agent_engine.approvals.models import RunRecord, RunStatus
from agent_engine.engine.approval_cancellation_engine import ApprovalCancellationEngine
from agent_engine.engine.approval_engine import ApprovalEngine
from agent_engine.engine.approval_streaming_engine import ApprovalStreamingEngine
from agent_engine.engine.engine import Engine
from agent_engine.engine.run_status_engine import RunStatusEngine
from agent_engine.engine.types import RunResult
from agent_engine.logging_config import log
from agent_engine.runs.repository import RunRepository
from agent_engine.runtime.hooks import AuthContext, RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agent_engine.runtime.tool_models import ToolUsageRecord
from agent_manager.application.context import build_history
from agent_manager.application.errors import (
    ConversationAccessDenied,
    ConversationAlreadyExists,
    ConversationBranchConflict,
    ConversationLinkRefused,
    ConversationMessageNotFound,
    ConversationNotFound,
    ConversationTokenBudgetExceeded,
)
from agent_manager.application.prepared_conversation_turn import PreparedConversationTurn
from agent_manager.domain import (
    ConversationMessage,
    ConversationSession,
    Page,
    PageRequest,
    Principal,
    Repository,
    Role,
    TitleGenerator,
    TokenBudgetUsage,
    thread_title,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _run_context(turn: PreparedConversationTurn) -> RunContext:
    """The per-run context one turn executes under, carrying the caller's own
    host credential so tools act as this user rather than share a privileged key."""
    return RunContext(
        run_id=turn.run_id,
        conversation_id=turn.session_id,
        user_id=turn.user_id,
        auth_context=AuthContext(
            user_id=turn.user_id,
            organization_id=turn.principal.organization_id,
            roles=turn.principal.roles,
            inbound_access_token=turn.principal.access_token,
        ),
    )


class ConversationService:
    def __init__(
        self,
        engine: Engine,
        repository: Repository,
        *,
        window: int = 10,
        max_chars: int | None = None,
        max_tokens: int | None = None,
        snapshot_ttl_seconds: int | None = 86_400,
        system_name: str | None = None,
        config_path: str | None = None,
        run_repository: RunRepository | None = None,
        title_generator: TitleGenerator | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._window = window
        self._max_chars = max_chars
        self._max_tokens = max_tokens
        self._snapshot_ttl_seconds = snapshot_ttl_seconds
        self._system_name = system_name
        self._config_path = config_path
        self._run_repository = run_repository
        self._title_generator = title_generator
        self._background: set[asyncio.Task[Any]] = set()

    async def close(self) -> None:
        """Let background work finish before the process goes down."""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    async def wait_for_generated_title(self, turn: PreparedConversationTurn) -> str | None:
        """This conversation's settled title, once the turn's naming work lands.

        `None` means the turn started no naming, generation itself failed, or
        the settled title could not be persisted. Otherwise this is the stored
        title — generated, or the trimmed opening message that stands in when
        the model cannot produce one.

        Cheap for whoever drives a live turn: naming starts with the turn, so
        by the time the turn's own stream ends this has usually finished
        already. Bounded by the generator's own timeout regardless.
        """
        if turn.title_task is None:
            return None
        # The request is only observing application-owned work. A disconnect
        # must not cancel the task that persists the conversation's title.
        return await asyncio.shield(turn.title_task)

    async def create(self, principal: Principal, *, session_id: str | None = None) -> str:
        """Create a conversation, or return the caller's own existing one.

        A caller-supplied `session_id` may already exist. Handing it back to its
        owner keeps creation idempotent; handing it to anyone else would be a
        takeover.
        """
        await self._register(principal)
        session = await self._repository.create_session(
            session_id,
            user_id=principal.user_id,
            system_name=self._system_name,
            config_path=self._config_path,
        )
        # Checked after the write, not before: a check first lets two callers
        # racing for one id both pass it.
        if session.user_id != principal.user_id:
            raise ConversationAlreadyExists(session.session_id)
        return session.session_id

    async def link_anonymous(self, visitor: Principal, principal: Principal) -> int:
        """Hand a visitor's conversations to the account they just signed into.

        One direction only: a signed-in user adopts a visitor's history, never
        the reverse, or one browser could push conversations onto another.
        """
        if principal.is_anonymous:
            raise ConversationLinkRefused(visitor.user_id)
        await self._register(principal)
        return await self._repository.link_anonymous_user(visitor.user_id, principal.user_id)

    async def history(
        self, conversation_id: str, principal: Principal
    ) -> list[ConversationMessage]:
        await self._authorize(conversation_id, principal)
        messages = await self._repository.list_conversation_messages(conversation_id)
        return await self._with_run_statuses(messages)

    async def usage(self, conversation_id: str, principal: Principal) -> TokenBudgetUsage:
        await self._authorize(conversation_id, principal)
        used = await self._repository.get_token_usage(conversation_id)
        return TokenBudgetUsage.from_totals(used, self._max_tokens)

    async def set_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        feedback: str,
        principal: Principal,
    ) -> ConversationMessage | None:
        await self._authorize(conversation_id, principal)
        return await self._repository.update_message_feedback(message_id, feedback)

    async def list_conversations(
        self, principal: Principal, page: PageRequest | None = None
    ) -> Page[ConversationSession]:
        return await self._repository.list_sessions(principal.user_id, page=page)

    async def send(
        self,
        conversation_id: str,
        text: str,
        principal: Principal,
        *,
        edit_message_id: str | None = None,
    ) -> RunResult:
        turn = await self.prepare_turn(
            conversation_id, text, principal, edit_message_id=edit_message_id
        )
        try:
            result = await self._engine.run(
                turn.message,
                history=turn.history,
                context=_run_context(turn),
            )
        except asyncio.CancelledError:
            await self.cancel_turn(turn)
            raise
        except Exception:
            await self.fail_turn(turn)
            raise
        if result.pending_approval is None:
            await self.complete_turn(turn, result)
        return result

    async def prepare_turn(
        self,
        conversation_id: str,
        text: str,
        principal: Principal,
        *,
        edit_message_id: str | None = None,
    ) -> PreparedConversationTurn:
        """Persist a user message and return its isolated prior model context."""
        await self._authorize(conversation_id, principal)
        user_id = principal.user_id
        await self._register(principal)

        if self._max_tokens is not None:
            used = await self._repository.get_token_usage(conversation_id)
            if used >= self._max_tokens:
                raise ConversationTokenBudgetExceeded(conversation_id)

        # Load prior history before saving the new message, or it gets inlined twice.
        session = await self._require(conversation_id)
        expected_head = session.head_message_id
        parent_message_id = expected_head
        if edit_message_id is None:
            prior_context = await self._repository.get_context(
                conversation_id,
                max_messages=self._window,
                max_chars=self._max_chars,
            )
        else:
            branch = await self._repository.list_conversation_messages(conversation_id)
            target = next(
                (
                    message
                    for message in branch
                    if message.message_id == edit_message_id and message.role == Role.USER
                ),
                None,
            )
            if target is None:
                raise ConversationMessageNotFound(edit_message_id)
            parent_message_id = target.parent_message_id
            prior_context = await self._repository.get_context_at(
                conversation_id,
                parent_message_id,
                max_messages=self._window,
                max_chars=self._max_chars,
            )
        run_id = uuid.uuid4().hex
        message_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        await self._register_run(run_id)
        try:
            appended = await self._repository.append_message_if_head(
                ConversationMessage(
                    message_id=message_id,
                    session_id=conversation_id,
                    run_id=run_id,
                    user_id=user_id,
                    role=Role.USER,
                    content=text,
                    created_at=now,
                    parent_message_id=parent_message_id,
                    status="running",
                ),
                expected_head,
                snapshot_ttl_seconds=self._snapshot_ttl_seconds,
            )
        except BaseException:
            await self._transition_run(run_id, RunStatus.CANCELLED)
            raise
        if not appended:
            await self._transition_run(run_id, RunStatus.CANCELLED)
            raise ConversationBranchConflict(conversation_id)
        # Title the conversation exactly once. Context is bounded and may be
        # empty for reasons unrelated to whether this is the first message, so
        # use the authoritative pre-append head instead.
        title_task = (
            await self._name_conversation(conversation_id, text) if expected_head is None else None
        )

        return PreparedConversationTurn(
            session_id=conversation_id,
            run_id=run_id,
            message_id=message_id,
            user_id=user_id,
            message=text,
            history=build_history(prior_context.messages, self._window),
            principal=principal,
            title_task=title_task,
        )

    async def complete_turn(
        self,
        turn: PreparedConversationTurn,
        result: RunResult,
    ) -> None:
        """Persist the final assistant response after any approval resumes finish."""
        if result.pending_approval is not None:
            raise ValueError("cannot complete a conversation turn while approval is pending")
        await self._persist_assistant_turn(
            session_id=turn.session_id,
            run_id=turn.run_id,
            user_id=turn.user_id,
            parent_message_id=turn.message_id,
            content=result.answer,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            visited=result.visited,
            used_tools=result.used_tools,
        )

    async def decide_approval(
        self,
        conversation_id: str,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision,
        principal: Principal,
    ) -> RunResult:
        """Resume one pending run as the owner of its conversation."""
        session = await self._authorize(conversation_id, principal)
        engine = self._require_approval_engine()
        try:
            result = await engine.resume(
                run_id,
                approval_id,
                decision,
                caller_user_id=session.user_id,
                caller_session_id=session.session_id,
                access_token=principal.access_token,
            )
        except ApprovalAlreadyProcessed:
            recovered = await engine.get_processed_result(
                run_id,
                approval_id,
                caller_user_id=session.user_id,
                caller_session_id=session.session_id,
            )
            if recovered is None:
                raise
            result = recovered
        if result.pending_approval is None:
            parent_message_id = await self._user_message_id_for_run(session.session_id, run_id)
            await self._persist_assistant_turn(
                session_id=session.session_id,
                run_id=run_id,
                user_id=session.user_id,
                parent_message_id=parent_message_id,
                content=result.answer,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                visited=result.visited,
                used_tools=result.used_tools,
            )
        return result

    async def cancel_pending_approval(
        self,
        conversation_id: str,
        run_id: str,
        approval_id: str,
        principal: Principal,
    ) -> None:
        """Terminally cancel a pending approval owned by this conversation."""
        session = await self._authorize(conversation_id, principal)
        engine = self._require_approval_cancellation_engine()
        await engine.cancel_pending_approval(
            run_id,
            approval_id,
            caller_user_id=session.user_id,
            caller_session_id=session.session_id,
        )

    async def stream_approval(
        self,
        conversation_id: str,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision,
        principal: Principal,
    ) -> AsyncIterator[RunStreamEvent]:
        """Authorize and return an owned stream for the same suspended run."""
        session = await self._authorize(conversation_id, principal)
        engine = self._require_approval_streaming_engine()
        parent_message_id = await self._user_message_id_for_run(session.session_id, run_id)

        async def events() -> AsyncIterator[RunStreamEvent]:
            engine_stream = engine.resume_stream(
                run_id,
                approval_id,
                decision,
                caller_user_id=session.user_id,
                caller_session_id=session.session_id,
                access_token=principal.access_token,
            )
            try:
                async for event in engine_stream:
                    if event.type == "final":
                        await self._persist_durably(
                            self._persist_assistant_turn(
                                session_id=session.session_id,
                                run_id=run_id,
                                user_id=session.user_id,
                                parent_message_id=parent_message_id,
                                content=event.content or "",
                                input_tokens=event.input_tokens,
                                output_tokens=event.output_tokens,
                                visited=event.route or (),
                                used_tools=event.used_tools,
                            )
                        )
                    yield event
            finally:
                if isinstance(engine_stream, AsyncGenerator):
                    await engine_stream.aclose()

        return events()

    async def stream(
        self,
        conversation_id: str,
        text: str,
        principal: Principal,
        *,
        edit_message_id: str | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        turn = await self.prepare_turn(
            conversation_id, text, principal, edit_message_id=edit_message_id
        )

        stream = self.stream_turn(turn)
        suspended = False
        exhausted = False
        try:
            async for event in stream:
                suspended = suspended or event.type == "pending_approval"
                yield event
            exhausted = True
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.fail_turn(turn)
            # The run is already terminal; `finally` must not try to cancel it.
            exhausted = True
            raise
        finally:
            try:
                await cast(AsyncGenerator[RunStreamEvent, None], stream).aclose()
            finally:
                if not exhausted and not suspended:
                    await self.cancel_turn(turn)

    async def cancel_turn(self, turn: PreparedConversationTurn) -> None:
        """Atomically cancel a prepared or executing run when its owner leaves."""
        await self._transition_run(turn.run_id, RunStatus.CANCELLED)

    async def fail_turn(self, turn: PreparedConversationTurn) -> None:
        """Fail a prepared run when execution raises before the engine records it."""
        await self._transition_run(turn.run_id, RunStatus.FAILED)

    async def stream_turn(self, turn: PreparedConversationTurn) -> AsyncIterator[RunStreamEvent]:
        """Execute one already-persisted turn under its request-owned stream."""

        engine_stream = self._engine.stream(
            turn.message,
            history=turn.history,
            context=_run_context(turn),
        )
        try:
            async for event in engine_stream:
                if event.type == "final":
                    await self._persist_stream_final(turn, event)

                yield event
        finally:
            if isinstance(engine_stream, AsyncGenerator):
                await engine_stream.aclose()

    async def _persist_stream_final(
        self, turn: PreparedConversationTurn, final: RunStreamEvent
    ) -> None:
        """Finish persistence before exposing a terminal answer downstream."""
        await self._persist_durably(
            self._persist_assistant_turn(
                session_id=turn.session_id,
                run_id=turn.run_id,
                user_id=turn.user_id,
                parent_message_id=turn.message_id,
                content=final.content or "",
                input_tokens=final.input_tokens,
                output_tokens=final.output_tokens,
                visited=final.route or (),
                used_tools=final.used_tools,
            )
        )

    async def _persist_durably(self, persistence: Coroutine[Any, Any, None]) -> None:
        """Complete one write even if the awaiting task is cancelled.

        Every path that exposes a terminal answer goes through here: an abort
        landing on the await must not leave a run terminal with no message.
        """
        task = asyncio.create_task(persistence)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _name_conversation(
        self, conversation_id: str, text: str
    ) -> asyncio.Task[str | None] | None:
        """Title a conversation from its opening message, once.

        The trimmed title lands first so the thread is never nameless, and a
        generated one overwrites it from the background: the caller's first
        token must not wait on a second model. Returns the background work so
        the caller can deliver its result without waiting on it here.
        """
        fallback = thread_title(text)
        fallback_persisted = await self._rename(conversation_id, fallback)
        generator = self._title_generator
        if generator is None:
            return None
        return self._spawn(
            self._generate_title(
                generator,
                conversation_id,
                text,
                persisted_title=fallback if fallback_persisted else None,
            )
        )

    async def _generate_title(
        self,
        generator: TitleGenerator,
        conversation_id: str,
        text: str,
        *,
        persisted_title: str | None,
    ) -> str | None:
        try:
            title = await generator.generate(text, conversation_id)
        except Exception:
            log(
                logger,
                logging.WARNING,
                "conversation title generation failed",
                conversation_id=conversation_id,
                exc_info=True,
            )
            return None
        if title == persisted_title:
            return title
        return title if await self._rename(conversation_id, title) else None

    async def _rename(self, conversation_id: str, title: str) -> bool:
        """Store a cosmetic title without letting failure orphan the turn."""
        try:
            await self._repository.rename_session(conversation_id, title)
            return True
        except Exception:
            log(
                logger,
                logging.WARNING,
                "conversation title update failed",
                conversation_id=conversation_id,
                exc_info=True,
            )
            return False

    def _spawn(self, work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Hold a background task for its whole life.

        The event loop keeps only a weak reference, so a task nobody owns can be
        garbage-collected mid-flight and simply never finish.
        """
        task = asyncio.create_task(work)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _persist_assistant_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        user_id: str | None,
        parent_message_id: str | None,
        content: str,
        input_tokens: int | None,
        output_tokens: int | None,
        visited: Sequence[str],
        used_tools: Sequence[ToolUsageRecord],
    ) -> None:
        """Persist one normalized final assistant response."""
        message_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"agent-manager:{session_id}:{run_id}:assistant",
        ).hex
        await self._repository.append_message_if_absent(
            ConversationMessage(
                message_id=message_id,
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                role=Role.ASSISTANT,
                content=content,
                created_at=datetime.now(UTC),
                parent_message_id=parent_message_id,
                status="completed",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                metadata={
                    "visited": list(visited),
                    "used_tools": [dataclasses.asdict(tool) for tool in used_tools],
                },
            ),
            snapshot_ttl_seconds=self._snapshot_ttl_seconds,
        )

    async def _user_message_id_for_run(self, session_id: str, run_id: str) -> str | None:
        message = await self._repository.get_user_message_for_run(session_id, run_id)
        return message.message_id if message is not None else None

    async def _with_run_statuses(
        self, messages: list[ConversationMessage]
    ) -> list[ConversationMessage]:
        completed = {
            message.run_id
            for message in messages
            if message.role == Role.ASSISTANT and message.run_id is not None
        }
        run_ids = {message.run_id for message in messages if message.run_id is not None}
        statuses: dict[str, str] = {}
        if self._run_repository is not None:
            records = await self._run_repository.get_many(run_ids)
            statuses = {run_id: record.status.value for run_id, record in records.items()}
        elif isinstance(self._engine, RunStatusEngine):
            for run_id in run_ids:
                with suppress(RunNotFound):
                    statuses[run_id] = await self._engine.get_run_status(run_id)
        return [
            dataclasses.replace(
                message,
                status=statuses.get(
                    message.run_id or "",
                    "completed" if message.run_id in completed else message.status,
                ),
            )
            for message in messages
        ]

    async def _register_run(self, run_id: str) -> None:
        if self._run_repository is None:
            return
        created = await self._run_repository.create_if_absent(
            RunRecord(
                run_id=run_id,
                thread_id=run_id,
                system_name=self._system_name or "",
            )
        )
        if not created:
            raise RuntimeError(f"generated run id already exists: {run_id}")

    async def _transition_run(self, run_id: str, target: RunStatus) -> None:
        if self._run_repository is not None:
            await self._run_repository.transition_if_allowed(run_id, target)

    async def _register(self, principal: Principal) -> None:
        await self._repository.upsert_user(
            principal.user_id,
            external_user_id=principal.external_id,
            display_name=principal.display_name,
        )

    def _require_approval_engine(self) -> ApprovalEngine:
        if not isinstance(self._engine, ApprovalEngine):
            raise RuntimeError("the configured engine does not support approval resume")
        return self._engine

    def _require_approval_cancellation_engine(self) -> ApprovalCancellationEngine:
        if not isinstance(self._engine, ApprovalCancellationEngine):
            raise RuntimeError("the configured engine does not support approval cancellation")
        return self._engine

    def _require_approval_streaming_engine(self) -> ApprovalStreamingEngine:
        if not isinstance(self._engine, ApprovalStreamingEngine):
            raise RuntimeError("the configured engine does not support approval streaming")
        return self._engine

    async def _require(self, conversation_id: str) -> ConversationSession:
        session = await self._repository.get_session(conversation_id)
        if session is None:
            raise ConversationNotFound(conversation_id)
        return session

    async def _authorize(self, conversation_id: str, principal: Principal) -> ConversationSession:
        """Resolve a conversation the caller owns.

        A turn runs as the session owner and hooks and tools authorize on
        `RunContext.user_id`, so knowing the conversation id must not be enough
        to reach it.
        """
        session = await self._require(conversation_id)
        if session.user_id != principal.user_id:
            raise ConversationAccessDenied(conversation_id)
        return session
