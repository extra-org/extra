"""No-network delivery processing under current authorization and durable state."""

import asyncio
from datetime import timedelta

import pytest

from agent_manager.application.channels.delivery import ChannelDeliveryService
from agent_manager.domain import Principal
from agent_manager.domain.channels.delivery import ChannelSender, SendOutcome
from agent_manager.infrastructure.channels.whatsapp.rendering import render_text
from tests.agent_manager.test_channel_delivery_storage import answered
from tests.agent_manager.test_channel_delivery_storage import delivery as delivery
from tests.agent_manager.test_channel_worker import ALICE, NOW, Identities
from tests.agent_manager.test_channel_worker import work as work


class Sender(ChannelSender):
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes or (SendOutcome("accepted", "wamid.one"),))
        self.calls = []

    async def send(self, recipient, text):
        self.calls.append((recipient, text))
        return self.outcomes.pop(0)


def pump(work, delivery, sender, *, now=NOW):
    return ChannelDeliveryService(
        delivery,
        work.identities,
        sender,
        connection_id="connection",
        system_id="system",
        organization_id="org",
        render=render_text,
        clock=lambda: now,
        jitter=lambda: 0.5,
    )


async def test_delivery_sends_saved_answer_without_another_model_call(work, delivery):
    item = await answered(work)
    sender = Sender()
    service = pump(work, delivery, sender)
    await service.tick(asyncio.Event())
    await service.tick(asyncio.Event())
    assert sender.calls == [("sender", "answer:hello")]
    assert work.engine.prompts == ["hello"]
    assert (await delivery.parts(item.event_id))[0].state == "accepted"


@pytest.mark.parametrize(
    "principal",
    [
        None,
        Principal.anonymous("v"),
        Principal.external("other", organization_id="org"),
        Principal.external("alice", organization_id="other"),
    ],
)
async def test_delivery_reauthorizes_original_owner_before_exposing_internal_text(
    work, delivery, principal
):
    item = await answered(work)
    work.identities = Identities(principal)
    sender = Sender()
    await pump(work, delivery, sender).tick(asyncio.Event())
    assert sender.calls == []
    assert (await delivery.parts(item.event_id))[0].state == "blocked_identity"


async def test_delivery_checks_window_again_on_retry_and_preserves_saved_text(work, delivery):
    item = await answered(work)
    sender = Sender(SendOutcome("retry", retry_after=1))
    await pump(work, delivery, sender).tick(asyncio.Event())
    part = (await delivery.parts(item.event_id))[0]
    assert part.state == "retry" and part.next_attempt_at > NOW
    await pump(work, delivery, sender, now=NOW + timedelta(days=1)).tick(asyncio.Event())
    assert len(sender.calls) == 1
    assert (await delivery.parts(item.event_id))[0].state == "blocked_window"
    assert work.engine.prompts == ["hello"]


async def test_unknown_submission_blocks_same_recipient_without_retry(work, delivery):
    item = await answered(work)
    await answered(work, "two")
    sender = Sender(SendOutcome("unknown"))
    service = pump(work, delivery, sender)
    await service.tick(asyncio.Event())
    await service.tick(asyncio.Event())
    assert len(sender.calls) == 1
    assert (await delivery.parts(item.event_id))[0].state == "delivery_unknown"


async def test_cancelled_send_is_joined_and_persisted_as_unknown(work, delivery):
    started = asyncio.Event()

    class BlockedSender(Sender):
        async def send(self, recipient, text):
            started.set()
            await asyncio.Event().wait()

    item = await answered(work)
    task = asyncio.create_task(pump(work, delivery, BlockedSender()).tick(asyncio.Event()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await delivery.parts(item.event_id))[0].state == "delivery_unknown"


async def test_shutdown_after_identity_lookup_prevents_network_submission(work, delivery):
    stop = asyncio.Event()

    class StopIdentity(Identities):
        async def resolve(self, address):
            stop.set()
            return ALICE

    await answered(work)
    work.identities = StopIdentity()
    sender = Sender()
    await pump(work, delivery, sender).tick(stop)
    assert sender.calls == []


async def test_each_part_refreshes_identity_and_revocation_stops_remaining_parts(work, delivery):
    item = await work.claim(content="x" * 9000)
    await work.processor().process(item)
    sender = Sender()
    await pump(work, delivery, sender).tick(asyncio.Event())
    work.identities = Identities(None)
    await pump(work, delivery, sender).tick(asyncio.Event())
    assert len(sender.calls) == 1
    assert [part.state for part in await delivery.parts(item.event_id)] == [
        "accepted",
        "blocked_identity",
        "ready",
    ]


async def test_lost_acceptance_commit_becomes_unknown_without_regenerating_or_resending(
    work, delivery, monkeypatch
):
    item = await answered(work)
    original = delivery.complete

    async def unavailable(*args, **kwargs):
        raise OSError("private-storage-credential")

    sender = Sender()
    monkeypatch.setattr(delivery, "complete", unavailable)
    with pytest.raises(OSError):
        await pump(work, delivery, sender).tick(asyncio.Event())
    monkeypatch.setattr(delivery, "complete", original)
    await pump(work, delivery, sender).tick(asyncio.Event())
    assert len(sender.calls) == 1 and work.engine.prompts == ["hello"]
    assert (await delivery.parts(item.event_id))[0].state == "delivery_unknown"


async def test_known_retries_stop_at_the_attempt_budget(work, delivery):
    item = await answered(work)
    sender = Sender(*(SendOutcome("retry") for _ in range(4)))
    current = NOW
    for _ in range(4):
        await pump(work, delivery, sender, now=current).tick(asyncio.Event())
        part = (await delivery.parts(item.event_id))[0]
        if part.next_attempt_at:
            current = part.next_attempt_at
    await pump(work, delivery, sender, now=current + timedelta(seconds=100)).tick(asyncio.Event())
    assert len(sender.calls) == 4 and part.state == "failed"


async def test_identity_failure_is_sanitized_and_never_exposes_the_saved_answer(
    work, delivery, caplog
):
    item = await answered(work)
    work.identities = Identities(RuntimeError("private-identity-credential"))
    sender = Sender()
    await pump(work, delivery, sender).tick(asyncio.Event())
    assert sender.calls == []
    assert (await delivery.parts(item.event_id))[0].state == "blocked_identity"
    assert "private-identity-credential" not in caplog.text
    assert "answer:hello" not in caplog.text
