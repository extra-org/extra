"""ConversationService use cases — pure: in-memory repository + stub engine."""

from __future__ import annotations

import pytest

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


async def test_prior_history_passed_to_engine_not_duplicated() -> None:
    service, engine = _service()
    cid = await service.create()
    await service.send(cid, "turn on kitchen lights")
    await service.send(cid, "now turn it off")

    assert engine.prompts[0] == "turn on kitchen lights"
    second = engine.prompts[1]
    assert "turn on kitchen lights" in second
    assert second.count("now turn it off") == 1
    assert second.rstrip().endswith("now turn it off")


async def test_window_caps_history_sent_to_engine() -> None:
    service, engine = _service(window=2)
    cid = await service.create()
    for i in range(4):
        await service.send(cid, f"msg{i}")

    last = engine.prompts[-1]
    assert "msg3" in last
    assert "msg0" not in last


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
