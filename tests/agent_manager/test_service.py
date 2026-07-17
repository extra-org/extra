"""ConversationService use cases — pure: in-memory repository + stub engine."""

from __future__ import annotations

import asyncio

import pytest

from agent_engine.engine.types import ChatMessage, ChatRole
from agent_manager.application import ConversationNotFound, ConversationService
from agent_manager.domain import Role
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from tests.agent_manager.conftest import RecordingEngine


def _service(window: int = 10) -> tuple[ConversationService, RecordingEngine]:
    engine = RecordingEngine()
    return ConversationService(engine, MemoryRepository(), window=window), engine


async def test_send_persists_user_and_assistant_in_order() -> None:
    service, _ = _service()
    cid = await service.create()
    await service.send(cid, "hello")

    msgs = await service.history(cid)
    assert [(m.role, m.content) for m in msgs] == [
        (Role.USER, "hello"),
        (Role.ASSISTANT, "answer:hello"),
    ]


async def test_prior_history_passed_to_engine_as_structured_messages() -> None:
    service, engine = _service()
    cid = await service.create()
    await service.send(cid, "turn on kitchen lights")
    await service.send(cid, "now turn it off")

    assert engine.prompts[0] == "turn on kitchen lights"
    assert engine.prompts[1] == "now turn it off"
    assert engine.histories[0] == ()
    assert engine.histories[1] == (
        ChatMessage(ChatRole.USER, "turn on kitchen lights"),
        ChatMessage(ChatRole.ASSISTANT, "answer:turn on kitchen lights"),
    )


async def test_window_caps_history_sent_to_engine() -> None:
    service, engine = _service(window=2)
    cid = await service.create()
    for i in range(4):
        await service.send(cid, f"msg{i}")

    assert engine.prompts[-1] == "msg3"
    assert [message.content for message in engine.histories[-1]] == [
        "msg2",
        "answer:msg2",
    ]


async def test_unknown_conversation_raises() -> None:
    service, _ = _service()
    with pytest.raises(ConversationNotFound):
        await service.send("missing", "hi")
    with pytest.raises(ConversationNotFound):
        await service.history("missing")


async def test_send_uses_stable_session_and_unique_run_id() -> None:
    service, engine = _service()
    cid = await service.create(user_id="u1", session_id="sess-1")
    await service.send(cid, "first", user_id="u1")
    await service.send(cid, "second", user_id="u1")

    contexts = [ctx for ctx in engine.contexts if ctx is not None]
    assert [ctx.conversation_id for ctx in contexts] == ["sess-1", "sess-1"]
    assert [ctx.user_id for ctx in contexts] == ["u1", "u1"]
    assert contexts[0].run_id is not None
    assert contexts[1].run_id is not None
    assert contexts[0].run_id != contexts[1].run_id


async def test_service_creates_user_and_session_metadata() -> None:
    service, _ = _service()
    cid = await service.create(user_id="u1", session_id="sess-1")

    assert cid == "sess-1"
    repo = service._repository
    assert await repo.get_user("u1") is not None
    session = await repo.get_session("sess-1")
    assert session is not None
    assert session.user_id == "u1"


async def test_new_session_receives_no_previous_history() -> None:
    service, engine = _service()
    first = await service.create(session_id="session-one")
    await service.send(first, "offer numbered options")
    second = await service.create(session_id="session-two")

    await service.send(second, "1")

    assert engine.prompts[-1] == "1"
    assert engine.histories[-1] == ()


async def test_concurrent_sessions_do_not_leak_history() -> None:
    service, engine = _service()
    first = await service.create(session_id="session-one")
    second = await service.create(session_id="session-two")
    await service.send(first, "first private context")
    await service.send(second, "second private context")

    await asyncio.gather(
        service.send(first, "follow up one"),
        service.send(second, "follow up two"),
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
