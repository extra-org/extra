"""ConversationService use cases — pure: in-memory repository + stub engine."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import cast

import pytest

from agent_engine.approvals.errors import RunNotFound
from agent_engine.engine.run_status_engine import RunStatusEngine
from agent_engine.engine.types import ChatMessage, ChatRole
from agent_engine.runtime.hooks.models import RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.application import (
    ConversationAccessDenied,
    ConversationAlreadyExists,
    ConversationLinkRefused,
    ConversationNotFound,
    ConversationService,
)
from agent_manager.domain import MessageFeedback, Principal, Role
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from tests.agent_manager.conftest import RecordingEngine

ALICE = Principal.external("alice")
BOB = Principal.external("bob")
VISITOR = Principal.anonymous("visitor-1")


class CleanupAfterFinalError(RuntimeError):
    """Distinct failure type used to verify transparent propagation."""


class FinalThenCleanupErrorEngine(RecordingEngine):
    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        yield RunStreamEvent(type="final", content="persisted", route=("agent",))
        raise CleanupAfterFinalError("cleanup after final")


class RenameFailingRepository(MemoryRepository):
    async def rename_session(self, session_id: str, title: str) -> None:
        del session_id, title
        raise RuntimeError("title storage unavailable")


class CancellableThenSuccessfulEngine(RecordingEngine):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = asyncio.Event()
        self.stream_count = 0
        self.statuses: dict[str, str] = {}

    async def get_run_status(self, run_id: str) -> str:
        try:
            return self.statuses[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        self.prompts.append(message)
        self.histories.append(tuple(history))
        self.contexts.append(context)
        assert context is not None and context.run_id is not None
        self.statuses[context.run_id] = "running"
        self.stream_count += 1
        if self.stream_count > 1:
            self.statuses[context.run_id] = "completed"
            yield RunStreamEvent(type="final", content=f"answer:{message}", route=("agent",))
            return
        try:
            yield RunStreamEvent(type="answer_delta", content="partial")
            await asyncio.Event().wait()
        finally:
            self.statuses[context.run_id] = "cancelled"
            self.cancelled.set()


def test_run_status_capability_remains_structural() -> None:
    engine = CancellableThenSuccessfulEngine()

    assert isinstance(engine, RunStatusEngine)


def _service(window: int = 10) -> tuple[ConversationService, RecordingEngine]:
    engine = RecordingEngine()
    return ConversationService(engine, MemoryRepository(), window=window), engine


async def test_send_persists_user_and_assistant_in_order() -> None:
    service, _ = _service()
    cid = await service.create(ALICE)
    await service.send(cid, "hello", ALICE)

    msgs = await service.history(cid, ALICE)
    assert [(m.role, m.content) for m in msgs] == [
        (Role.USER, "hello"),
        (Role.ASSISTANT, "answer:hello"),
    ]


async def test_cosmetic_title_failure_does_not_orphan_an_accepted_turn() -> None:
    repository = RenameFailingRepository()
    service = ConversationService(RecordingEngine(), repository)
    cid = await service.create(ALICE)

    result = await service.send(cid, "hello", ALICE)

    assert result.answer == "answer:hello"
    messages = await service.history(cid, ALICE)
    assert [message.content for message in messages] == ["hello", "answer:hello"]


async def test_stream_persists_final_before_propagating_late_engine_failure() -> None:
    repository = MemoryRepository()
    service = ConversationService(FinalThenCleanupErrorEngine(), repository)
    cid = await service.create(ALICE)

    with pytest.raises(CleanupAfterFinalError, match="cleanup after final"):
        async for _event in service.stream(cid, "hello", ALICE):
            pass

    messages = await service.history(cid, ALICE)
    assert [(message.role, message.content) for message in messages] == [
        (Role.USER, "hello"),
        (Role.ASSISTANT, "persisted"),
    ]


async def test_stream_persists_final_when_consumer_closes_generator() -> None:
    repository = MemoryRepository()
    service = ConversationService(FinalThenCleanupErrorEngine(), repository)
    cid = await service.create(ALICE)
    events = cast(AsyncGenerator[RunStreamEvent, None], service.stream(cid, "hello", ALICE))

    final = await events.__anext__()
    assert final.type == "final"
    messages = await service.history(cid, ALICE)
    assert [(message.role, message.content) for message in messages] == [
        (Role.USER, "hello"),
        (Role.ASSISTANT, "persisted"),
    ]
    await events.aclose()


async def test_cancelled_stream_persists_no_partial_assistant_and_next_turn_succeeds() -> None:
    repository = MemoryRepository()
    engine = CancellableThenSuccessfulEngine()
    service = ConversationService(engine, repository)
    cid = await service.create(ALICE)
    events = cast(AsyncGenerator[RunStreamEvent, None], service.stream(cid, "first", ALICE))

    partial = await events.__anext__()
    assert partial.content == "partial"
    await events.aclose()
    await asyncio.wait_for(engine.cancelled.wait(), timeout=1)

    messages = await service.history(cid, ALICE)
    assert [(message.role, message.content) for message in messages] == [(Role.USER, "first")]
    assert messages[0].status == "cancelled"

    assert [event.type async for event in service.stream(cid, "second", ALICE)] == ["final"]
    messages = await service.history(cid, ALICE)
    assert [(message.role, message.content) for message in messages] == [
        (Role.USER, "first"),
        (Role.USER, "second"),
        (Role.ASSISTANT, "answer:second"),
    ]
    assert [message.content for message in engine.histories[-1]] == ["first"]
    assert all(message.content != "Generation stopped" for message in engine.histories[-1])


async def test_edit_creates_an_immutable_branch_with_only_ancestor_context() -> None:
    repository = MemoryRepository()
    engine = RecordingEngine()
    service = ConversationService(engine, repository)
    cid = await service.create(ALICE)
    await service.send(cid, "U1", ALICE)
    await service.send(cid, "U2", ALICE)
    original = await service.history(cid, ALICE)
    original_u2 = next(message for message in original if message.content == "U2")
    original_a2 = next(message for message in original if message.content == "answer:U2")

    await service.send(
        cid,
        "U2 edited",
        ALICE,
        edit_message_id=original_u2.message_id,
    )

    assert engine.histories[-1] == (
        ChatMessage(ChatRole.USER, "U1"),
        ChatMessage(ChatRole.ASSISTANT, "answer:U1"),
    )
    active = await service.history(cid, ALICE)
    assert [message.content for message in active] == [
        "U1",
        "answer:U1",
        "U2 edited",
        "answer:U2 edited",
    ]
    stored_u2 = await repository.get_message(original_u2.message_id)
    stored_a2 = await repository.get_message(original_a2.message_id)
    assert stored_u2 is not None and stored_u2.content == "U2"
    assert stored_a2 is not None and stored_a2.content == "answer:U2"
    edited = next(message for message in active if message.content == "U2 edited")
    assert edited.run_id != original_u2.run_id
    assert edited.parent_message_id == original_u2.parent_message_id


async def test_editing_root_starts_a_new_root_without_old_branch_context() -> None:
    repository = MemoryRepository()
    engine = RecordingEngine()
    service = ConversationService(engine, repository)
    cid = await service.create(ALICE)
    await service.send(cid, "original root", ALICE)
    original = await service.history(cid, ALICE)

    await service.send(
        cid,
        "edited root",
        ALICE,
        edit_message_id=original[0].message_id,
    )

    assert engine.histories[-1] == ()
    active = await service.history(cid, ALICE)
    assert [message.content for message in active] == [
        "edited root",
        "answer:edited root",
    ]
    assert active[0].parent_message_id is None
    stored_user = await repository.get_message(original[0].message_id)
    stored_assistant = await repository.get_message(original[1].message_id)
    assert stored_user is not None and stored_user.content == "original root"
    assert stored_assistant is not None and stored_assistant.content == "answer:original root"


async def test_prior_history_passed_to_engine_as_structured_messages() -> None:
    service, engine = _service()
    cid = await service.create(ALICE)
    await service.send(cid, "turn on kitchen lights", ALICE)
    await service.send(cid, "now turn it off", ALICE)

    assert engine.prompts[0] == "turn on kitchen lights"
    assert engine.prompts[1] == "now turn it off"
    assert engine.histories[0] == ()
    assert engine.histories[1] == (
        ChatMessage(ChatRole.USER, "turn on kitchen lights"),
        ChatMessage(ChatRole.ASSISTANT, "answer:turn on kitchen lights"),
    )


async def test_window_caps_history_sent_to_engine() -> None:
    service, engine = _service(window=2)
    cid = await service.create(ALICE)
    for i in range(4):
        await service.send(cid, f"msg{i}", ALICE)

    assert engine.prompts[-1] == "msg3"
    assert [message.content for message in engine.histories[-1]] == [
        "msg2",
        "answer:msg2",
    ]


async def test_unknown_conversation_raises() -> None:
    service, _ = _service()
    with pytest.raises(ConversationNotFound):
        await service.send("missing", "hi", ALICE)
    with pytest.raises(ConversationNotFound):
        await service.history("missing", ALICE)


async def test_send_uses_stable_session_and_unique_run_id() -> None:
    service, engine = _service()
    cid = await service.create(ALICE, session_id="sess-1")
    await service.send(cid, "first", ALICE)
    await service.send(cid, "second", ALICE)

    contexts = [ctx for ctx in engine.contexts if ctx is not None]
    assert [ctx.conversation_id for ctx in contexts] == ["sess-1", "sess-1"]
    assert [ctx.user_id for ctx in contexts] == [ALICE.user_id, ALICE.user_id]
    assert contexts[0].run_id is not None
    assert contexts[1].run_id is not None
    assert contexts[0].run_id != contexts[1].run_id


async def test_a_turn_carries_the_callers_own_credential_to_plugin_code() -> None:
    service, engine = _service()
    caller = dataclasses.replace(ALICE, access_token="host-token-abc", roles=("editor",))
    cid = await service.create(caller, session_id="sess-1")

    await service.send(cid, "hi", caller)

    context = engine.contexts[-1]
    assert context is not None and context.auth_context is not None
    auth = context.auth_context
    assert auth.inbound_access_token == "host-token-abc"
    assert auth.roles == ("editor",)


async def test_a_visitor_turn_carries_no_credential() -> None:
    """Plugin code gets nothing to act with, and must fail rather than fall back."""
    service, engine = _service()
    cid = await service.create(VISITOR, session_id="sess-v")

    await service.send(cid, "hi", VISITOR)

    context = engine.contexts[-1]
    assert context is not None and context.auth_context is not None
    assert context.auth_context.inbound_access_token is None


async def test_turn_refuses_a_caller_who_does_not_own_the_conversation() -> None:
    """Knowing a conversation id must not confer its owner's identity — the turn
    runs as the owner, and hooks and tools authorize on RunContext.user_id."""
    service, engine = _service()
    cid = await service.create(ALICE, session_id="sess-1")

    with pytest.raises(ConversationAccessDenied):
        await service.send(cid, "hi", BOB)
    with pytest.raises(ConversationAccessDenied):
        await service.send(cid, "hi", VISITOR)

    assert engine.contexts == []
    assert await service.history(cid, ALICE) == []


async def test_reads_of_an_owned_conversation_refuse_other_callers() -> None:
    service, _ = _service()
    cid = await service.create(ALICE, session_id="sess-1")
    await service.send(cid, "hi", ALICE)

    for caller in (BOB, VISITOR):
        with pytest.raises(ConversationAccessDenied):
            await service.history(cid, caller)
        with pytest.raises(ConversationAccessDenied):
            await service.usage(cid, caller)

    assert (await service.list_conversations(BOB)).items == []
    assert (await service.list_conversations(VISITOR)).items == []


async def test_create_refuses_a_session_id_owned_by_someone_else() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="sess-1")

    with pytest.raises(ConversationAlreadyExists):
        await service.create(BOB, session_id="sess-1")
    with pytest.raises(ConversationAlreadyExists):
        await service.create(VISITOR, session_id="sess-1")

    session = await service._repository.get_session("sess-1")
    assert session is not None
    assert session.user_id == ALICE.user_id


async def test_create_is_idempotent_for_the_owner() -> None:
    """agentctl reuses a --session id across runs, so the owner re-creating is
    the normal path, not an attack."""
    service, _ = _service()
    first = await service.create(ALICE, session_id="sess-1")

    assert await service.create(ALICE, session_id="sess-1") == first


async def test_service_creates_user_and_session_metadata() -> None:
    service, _ = _service()
    cid = await service.create(ALICE, session_id="sess-1")

    assert cid == "sess-1"
    repo = service._repository
    assert await repo.get_user(ALICE.user_id) is not None
    session = await repo.get_session("sess-1")
    assert session is not None
    assert session.user_id == ALICE.user_id


async def test_new_session_receives_no_previous_history() -> None:
    service, engine = _service()
    first = await service.create(ALICE, session_id="session-one")
    await service.send(first, "offer numbered options", ALICE)
    second = await service.create(ALICE, session_id="session-two")

    await service.send(second, "1", ALICE)

    assert engine.prompts[-1] == "1"
    assert engine.histories[-1] == ()


async def test_concurrent_sessions_do_not_leak_history() -> None:
    service, engine = _service()
    first = await service.create(ALICE, session_id="session-one")
    second = await service.create(ALICE, session_id="session-two")
    await service.send(first, "first private context", ALICE)
    await service.send(second, "second private context", ALICE)

    await asyncio.gather(
        service.send(first, "follow up one", ALICE),
        service.send(second, "follow up two", ALICE),
    )

    contexts_and_histories = zip(engine.contexts[-2:], engine.histories[-2:], strict=True)
    history_by_session = {
        context.conversation_id: tuple(message.content for message in history)
        for context, history in contexts_and_histories
        if context is not None
    }
    assert history_by_session["session-one"] == (
        "first private context",
        "answer:first private context",
    )
    assert history_by_session["session-two"] == (
        "second private context",
        "answer:second private context",
    )


async def test_signing_in_moves_a_visitors_conversations_onto_their_account() -> None:
    """A visitor who chats before logging in keeps that history afterwards."""
    service, _ = _service()
    before_login = await service.create(VISITOR, session_id="pre-login")
    await service.send(before_login, "how much does it cost?", VISITOR)

    moved = await service.link_anonymous(VISITOR, ALICE)

    assert moved == 1
    assert [s.session_id for s in (await service.list_conversations(ALICE)).items] == ["pre-login"]
    assert (await service.list_conversations(VISITOR)).items == []
    assert [m.content for m in await service.history(before_login, ALICE)] == [
        "how much does it cost?",
        "answer:how much does it cost?",
    ]


async def test_a_visitor_pass_can_only_be_adopted_once() -> None:
    """Replaying a pass must not attach the same chats to a second account."""
    service, _ = _service()
    await service.create(VISITOR, session_id="pre-login")

    assert await service.link_anonymous(VISITOR, ALICE) == 1
    assert await service.link_anonymous(VISITOR, BOB) == 0

    assert [s.session_id for s in (await service.list_conversations(ALICE)).items] == ["pre-login"]
    assert (await service.list_conversations(BOB)).items == []


async def test_a_visitor_cannot_adopt_another_visitor() -> None:
    service, _ = _service()
    await service.create(VISITOR, session_id="pre-login")

    with pytest.raises(ConversationLinkRefused):
        await service.link_anonymous(VISITOR, Principal.anonymous("visitor-2"))


async def test_adopting_merges_into_conversations_the_account_already_had() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="signed-in")
    await service.create(VISITOR, session_id="pre-login")

    await service.link_anonymous(VISITOR, ALICE)

    assert {s.session_id for s in (await service.list_conversations(ALICE)).items} == {
        "signed-in",
        "pre-login",
    }


async def test_set_message_feedback_persists_and_returns_message() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="s1")
    await service.send("s1", "hello", ALICE)
    history = await service.history("s1", ALICE)
    assistant_message = next(m for m in history if m.role == Role.ASSISTANT)

    updated = await service.set_message_feedback(
        "s1", assistant_message.message_id, MessageFeedback.THUMBS_UP, ALICE
    )

    assert updated is not None
    assert updated.feedback == MessageFeedback.THUMBS_UP
    assert updated.metadata.get("feedback") == MessageFeedback.THUMBS_UP.value


async def test_set_message_feedback_returns_none_for_missing_message() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="s1")

    updated = await service.set_message_feedback(
        "s1", "no-such-id", MessageFeedback.THUMBS_UP, ALICE
    )

    assert updated is None


async def test_set_message_feedback_is_authorized() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="s1")
    await service.send("s1", "hello", ALICE)

    with pytest.raises(ConversationAccessDenied):
        await service.set_message_feedback("s1", "no-such-id", MessageFeedback.THUMBS_UP, BOB)


async def test_set_message_feedback_requires_assistant_role() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="s1")
    await service.send("s1", "hello", ALICE)
    history = await service.history("s1", ALICE)
    user_message = next(m for m in history if m.role == Role.USER)

    updated = await service.set_message_feedback(
        "s1", user_message.message_id, MessageFeedback.THUMBS_UP, ALICE
    )

    assert updated is None


async def test_set_message_feedback_requires_same_conversation() -> None:
    service, _ = _service()
    await service.create(ALICE, session_id="s1")
    await service.create(ALICE, session_id="s2")
    await service.send("s1", "hello", ALICE)
    history = await service.history("s1", ALICE)
    assistant_message = next(m for m in history if m.role == Role.ASSISTANT)

    updated = await service.set_message_feedback(
        "s2", assistant_message.message_id, MessageFeedback.THUMBS_UP, ALICE
    )

    assert updated is None
