"""Deterministic controls never create a model run or reveal internal history."""

import asyncio

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.application.channels.controls import ChannelControlService
from agent_manager.domain.channels.models import InboundMessage, InboxState
from agent_manager.infrastructure.persistence.channels.controls import SqlControlRepository
from agent_manager.infrastructure.persistence.channels.tables import ChannelBindingRow
from agent_manager.infrastructure.persistence.database import session_factory
from tests.agent_manager.test_channel_worker import ADDRESS, ALICE, NOW
from tests.agent_manager.test_channel_worker import work as work


def controls(work):
    return ChannelControlService(
        SqlControlRepository(session_factory(work.database)),
        work.repository,
        support_contact="support@example.test",
    )


@pytest.mark.parametrize(
    "kind,body,fragment",
    [
        ("help", "/help", "support@example.test"),
        ("reset", "/new", "שיחה חדשה"),
        ("start", "/start", "חודשה"),
        ("unsupported", "", "טקסט"),
    ],
)
async def test_controls_stage_static_replies_and_never_prepare_agent_turns(
    work, kind, body, fragment
):
    item = await work.claim(content=body, kind=kind)
    assert await controls(work).handle(item)
    assert fragment in (await work.repository.list_replies(item.event_id))[0].text
    assert work.engine.prompts == []
    assert (await work.repository.get_inbound(item.event_id)).run_id is None


async def test_reset_is_atomic_idempotent_preserves_history_and_changes_generation(work):
    old = await work.claim()
    await work.processor().process(old)
    before = await work.repository.get_binding(ADDRESS)
    item = await work.claim("reset", content="/new", kind="reset")
    assert await controls(work).handle(item)
    assert await controls(work).handle(item)
    after = await work.repository.get_binding(ADDRESS)
    assert after.generation == before.generation + 1 and after.conversation_id is None
    assert len(await work.service.history(before.conversation_id, ALICE)) == 2
    assert len(await work.repository.list_replies(item.event_id)) == 1


async def test_stop_applies_before_worker_and_start_does_not_execute_old_queued_text(work):
    old = await work.repository.ingest(InboundMessage(ADDRESS, "old", "old question", NOW, NOW))
    await work.repository.ingest(InboundMessage(ADDRESS, "stop", "/stop", NOW, NOW, "stop"))
    await work.repository.ingest(
        InboundMessage(ADDRESS, "during", "question while stopped", NOW, NOW)
    )
    await work.repository.ingest(InboundMessage(ADDRESS, "start", "/start", NOW, NOW, "start"))
    service = controls(work)
    for _ in range(4):
        item = await work.repository.claim_next("connection", "system")
        assert await service.handle(item)
    assert work.engine.prompts == []
    assert (await work.repository.get_inbound(old.event_id)).state is InboxState.DONE
    assert await work.repository.list_replies(old.event_id) == ()


async def test_unrecognized_command_stays_normal_user_text(work):
    item = await work.claim(content="/help me find a file")
    assert not await controls(work).handle(item)


async def test_delayed_duplicate_control_reset_cannot_clear_a_new_conversation(work, monkeypatch):
    await work.processor().process(await work.claim())
    item = await work.claim("reset", content="/new", kind="reset")
    repository = SqlControlRepository(session_factory(work.database))
    ready, release = asyncio.Event(), asyncio.Event()
    original = AsyncSession.get
    slow = None

    async def delay_binding(self, model, *args, **kwargs):
        result = await original(self, model, *args, **kwargs)
        if asyncio.current_task() is slow and model is ChannelBindingRow:
            ready.set()
            await release.wait()
        return result

    monkeypatch.setattr(AsyncSession, "get", delay_binding)
    slow = asyncio.create_task(repository.complete(item, None, reset=True))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        await repository.complete(item, None, reset=True)
        next_item = await work.claim("next")
        await work.repository.bind_conversation(
            next_item.event_id, next_item.claim_token, ALICE.user_id, "org", "new-conversation"
        )
    finally:
        release.set()
        await slow
    binding = await work.repository.get_binding(ADDRESS)
    assert binding.generation == 1 and binding.conversation_id == "new-conversation"


@pytest.mark.parametrize("path", ["legacy", "control"])
async def test_reset_reconciles_a_commit_between_inbox_and_binding_reads(work, monkeypatch, path):
    item = await work.claim(content="/new", kind="reset")
    ready, release = asyncio.Event(), asyncio.Event()
    original = AsyncSession.get
    slow = None

    async def operation():
        if path == "legacy":
            return await work.repository.reset_conversation(item.event_id, item.claim_token)
        await SqlControlRepository(session_factory(work.database)).complete(item, None, reset=True)
        return 1

    async def delay_binding(self, model, *args, **kwargs):
        if asyncio.current_task() is slow and model is ChannelBindingRow:
            ready.set()
            await release.wait()
        return await original(self, model, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", delay_binding)
    slow = asyncio.create_task(operation())
    try:
        await asyncio.wait_for(ready.wait(), 2)
        assert await operation() == 1
    finally:
        release.set()
    assert await slow == 1
    assert (await work.repository.get_binding(ADDRESS)).generation == 1
