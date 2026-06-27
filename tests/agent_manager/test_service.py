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
