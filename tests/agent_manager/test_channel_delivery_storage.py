"""Real local transactions for provider parts and recipient ordering."""

from datetime import timedelta

import pytest

from agent_manager.domain.channels.models import ChannelMessageConflict, InboundMessage
from agent_manager.domain.channels.webhooks import DeliveryEvent, WebhookBatch
from agent_manager.infrastructure.persistence.channels.delivery import SqlDeliveryRepository
from agent_manager.infrastructure.persistence.channels.webhooks import SqlWebhookRepository
from agent_manager.infrastructure.persistence.database import session_factory
from tests.agent_manager.test_channel_worker import ADDRESS, NOW
from tests.agent_manager.test_channel_worker import work as work


@pytest.fixture
def delivery(work):
    return SqlDeliveryRepository(session_factory(work.database))


async def answered(work, provider="one", sender="sender"):
    item = await work.claim(provider, sender=sender)
    await work.processor().process(item)
    return item


async def test_delivery_planning_is_immutable_atomic_and_reuses_source_ids(work, delivery):
    item = await answered(work)
    sources = await delivery.sources("connection", "system")
    assert [(source.event_id, source.texts) for source in sources] == [
        (item.event_id, ("answer:hello",))
    ]
    await delivery.materialize(item.event_id, ("part one", "part two"))
    first = await delivery.parts(item.event_id)
    await delivery.materialize(item.event_id, ("part one", "part two"))
    assert await delivery.parts(item.event_id) == first
    assert [part.text for part in first] == ["part one", "part two"]
    assert await delivery.sources("connection", "system") == ()
    with pytest.raises(ChannelMessageConflict):
        await delivery.materialize(item.event_id, ("replacement",))


async def test_parts_and_turns_wait_for_prior_acceptance_but_other_senders_progress(work, delivery):
    one = await answered(work)
    two = await answered(work, "two")
    other = await answered(work, "other", "other")
    for item, texts in ((one, ("a", "b")), (two, ("c",)), (other, ("d",))):
        await delivery.materialize(item.event_id, texts)
    first = await delivery.claim_next("connection", "system", NOW)
    assert first.event_id == one.event_id and first.attempts == 1
    independent = await delivery.claim_next("connection", "system", NOW)
    assert independent.event_id == other.event_id
    assert await delivery.claim_next("connection", "system", NOW) is None
    await delivery.complete(first, "accepted", provider_id="wamid.a")
    second = await delivery.claim_next("connection", "system", NOW)
    assert second.event_id == one.event_id and second.part_index == 1
    await delivery.complete(second, "accepted", provider_id="wamid.b")
    assert (await delivery.claim_next("connection", "system", NOW)).event_id == two.event_id


async def test_retry_due_time_and_abandoned_submission_never_resend(work, delivery):
    item = await answered(work)
    await delivery.materialize(item.event_id, ("a",))
    part = await delivery.claim_next("connection", "system", NOW)
    await delivery.complete(part, "retry", next_attempt_at=NOW + timedelta(seconds=10))
    assert await delivery.claim_next("connection", "system", NOW) is None
    retried = await delivery.claim_next("connection", "system", NOW + timedelta(seconds=10))
    assert retried.delivery_id == part.delivery_id and retried.attempts == 2
    await delivery.recover_sending("connection", "system")
    assert (await delivery.parts(item.event_id))[0].state == "delivery_unknown"
    assert await delivery.claim_next("connection", "system", NOW + timedelta(days=1)) is None


async def test_guard_uses_newest_verified_inbound_time_and_does_not_reopen_from_status(
    work, delivery
):
    item = await answered(work)
    await delivery.materialize(item.event_id, ("a",))
    part = await delivery.claim_next("connection", "system", NOW)
    assert await delivery.guard(part, NOW + timedelta(hours=24)) == "blocked_window"
    await work.repository.ingest(
        InboundMessage(ADDRESS, "late", "late", NOW, NOW - timedelta(days=2))
    )
    assert await delivery.guard(part, NOW + timedelta(hours=24)) == "blocked_window"
    await work.repository.ingest(
        InboundMessage(ADDRESS, "new", "new", NOW + timedelta(hours=23), NOW + timedelta(hours=23))
    )
    assert await delivery.guard(part, NOW + timedelta(hours=24)) is None


async def test_stop_and_start_never_resurrect_previously_pending_replies(work, delivery):
    item = await answered(work)
    await delivery.materialize(item.event_id, ("a",))
    part = await delivery.claim_next("connection", "system", NOW)
    await work.repository.ingest(InboundMessage(ADDRESS, "stop", "/stop", NOW, NOW, "stop"))
    assert await delivery.guard(part, NOW) == "suppressed"
    await work.repository.ingest(InboundMessage(ADDRESS, "start", "/start", NOW, NOW, "start"))
    assert await delivery.guard(part, NOW) == "suppressed"


async def test_projection_handles_early_callbacks_exact_recipient_and_late_failures(work, delivery):
    item = await answered(work)
    await delivery.materialize(item.event_id, ("a",))
    part = await delivery.claim_next("connection", "system", NOW)
    webhook = SqlWebhookRepository(session_factory(work.database))
    await webhook.accept(
        "connection",
        "system",
        "a" * 64,
        NOW,
        WebhookBatch(
            delivery_events=(
                DeliveryEvent("wamid.a", "other", "read", NOW),
                DeliveryEvent("wamid.a", "sender", "delivered", NOW),
            )
        ),
    )
    await delivery.project("connection", "system")
    await delivery.complete(part, "accepted", provider_id="wamid.a")
    await delivery.project("connection", "system")
    assert (await delivery.parts(item.event_id))[0].state == "delivered"
    await webhook.accept(
        "connection",
        "system",
        "b" * 64,
        NOW,
        WebhookBatch(
            delivery_events=(
                DeliveryEvent("wamid.a", "sender", "sent", NOW - timedelta(seconds=1)),
                DeliveryEvent("wamid.a", "sender", "failed", NOW + timedelta(seconds=1)),
                DeliveryEvent("wamid.a", "sender", "read", NOW + timedelta(seconds=2)),
            )
        ),
    )
    await delivery.project("connection", "system")
    assert (await delivery.parts(item.event_id))[0].state == "read"


async def test_many_blocked_parts_cannot_starve_an_independent_recipient(work, delivery):
    item = await answered(work)
    other = await answered(work, "independent", "other")
    await delivery.materialize(item.event_id, tuple("a" for _ in range(40)))
    await delivery.materialize(other.event_id, ("other",))
    first = await delivery.claim_next("connection", "system", NOW)
    await delivery.complete(first, "retry", next_attempt_at=NOW + timedelta(hours=1))
    next_part = await delivery.claim_next("connection", "system", NOW)
    assert next_part is not None and next_part.event_id == other.event_id


async def test_an_unplanned_earlier_answer_prevents_later_recipient_delivery(work, delivery):
    await answered(work)
    later = await answered(work, "later")
    await delivery.materialize(later.event_id, ("later",))
    assert await delivery.claim_next("connection", "system", NOW) is None


async def test_internal_source_without_conversation_owner_cannot_become_public(work, delivery):
    from sqlalchemy import update
    from sqlmodel import col

    from agent_manager.infrastructure.persistence.channels.tables import ChannelInboxRow

    item = await answered(work)
    async with session_factory(work.database)() as session, session.begin():
        await session.exec(
            update(ChannelInboxRow)
            .where(col(ChannelInboxRow.event_id) == item.event_id)
            .values(conversation_id=None)
        )
    await delivery.materialize(item.event_id, ("internal answer",))
    assert await delivery.claim_next("connection", "system", NOW) is None


async def test_late_delivery_proof_recovers_a_failed_callback_without_resending_the_part(
    work, delivery
):
    item = await answered(work)
    await delivery.materialize(item.event_id, ("a", "b"))
    first = await delivery.claim_next("connection", "system", NOW)
    await delivery.complete(first, "accepted", provider_id="wamid.a")
    webhook = SqlWebhookRepository(session_factory(work.database))
    await webhook.accept(
        "connection",
        "system",
        "f" * 64,
        NOW,
        WebhookBatch(delivery_events=(DeliveryEvent("wamid.a", "sender", "failed", NOW),)),
    )
    await delivery.project("connection", "system")
    assert await delivery.claim_next("connection", "system", NOW) is None
    await webhook.accept(
        "connection",
        "system",
        "d" * 64,
        NOW,
        WebhookBatch(delivery_events=(DeliveryEvent("wamid.a", "sender", "delivered", NOW),)),
    )
    await delivery.project("connection", "system")
    second = await delivery.claim_next("connection", "system", NOW)
    assert second is not None and second.part_index == 1
    assert (await delivery.parts(item.event_id))[0].reason is None


async def test_reopened_storage_preserves_acceptance_and_holds_an_interrupted_next_part(
    work, delivery
):
    from agent_manager.infrastructure.persistence.database import create_db_engine

    item = await answered(work)
    await delivery.materialize(item.event_id, ("a", "b"))
    first = await delivery.claim_next("connection", "system", NOW)
    await delivery.complete(first, "accepted", provider_id="wamid.a")
    await delivery.claim_next("connection", "system", NOW)
    url = str(work.database.url)
    await work.database.dispose()
    reopened = create_db_engine(url)
    try:
        repository = SqlDeliveryRepository(session_factory(reopened))
        await repository.recover_sending("connection", "system")
        parts = await repository.parts(item.event_id)
        assert [part.state for part in parts] == ["accepted", "delivery_unknown"]
        assert parts[0].provider_message_id == "wamid.a"
        assert await repository.claim_next("connection", "system", NOW) is None
    finally:
        await reopened.dispose()
