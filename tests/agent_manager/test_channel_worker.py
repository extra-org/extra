"""Turn execution/recovery through manager ports and real local SQL."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from agent_engine.approvals.models import RunStatus
from agent_engine.engine.types import RunResult
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.application.channels.preparation import ChannelTurnPreparer
from agent_manager.application.channels.worker_options import ChannelWorkerOptions
from agent_manager.application.conversation_service import ConversationService
from agent_manager.application.errors import ConversationAccessDenied
from agent_manager.domain import Principal
from agent_manager.domain.channels.identity import ChannelIdentityResolver
from agent_manager.domain.channels.models import ChannelAddress, InboundMessage, InboxState
from agent_manager.domain.channels.ownership import (
    ChannelWorkerOwnership,
    ChannelWorkerOwnershipLost,
)
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.run_repository import SqlRunRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from tests.agent_manager.conftest import RecordingEngine

ALICE = Principal.external("alice", organization_id="org", roles=("employee",))
ADDRESS = ChannelAddress("connection", "system", "sender")
OPTIONS = ChannelWorkerOptions("connection", "system", "org", poll_seconds=0.01, drain_seconds=0.1)
NOW = datetime(2026, 10, 16, tzinfo=UTC)


class Identities(ChannelIdentityResolver):
    def __init__(self, *principals):
        self.principals = list(principals or (ALICE,))
        self.calls = 0

    async def resolve(self, address):
        self.calls += 1
        value = self.principals[min(self.calls - 1, len(self.principals) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


@dataclass
class Work:
    repository: SqlChannelRepository
    service: ConversationService
    engine: RecordingEngine
    runs: SqlRunRepository
    identities: Identities
    database: AsyncEngine

    def preparer(self):
        return ChannelTurnPreparer(
            self.service,
            self.repository,
            self.identities,
            connection_id="connection",
            system_id="system",
            organization_id="org",
        )

    def processor(self, options=OPTIONS):
        from agent_manager.application.channels.processing import ChannelTurnProcessor

        return ChannelTurnProcessor(
            self.service, self.repository, self.runs, self.preparer(), options
        )

    async def claim(self, provider_id="one", *, sender="sender", content="hello", kind="text"):
        await self.repository.ingest(
            InboundMessage(replace(ADDRESS, sender_id=sender), provider_id, content, NOW, NOW, kind)
        )
        item = await self.repository.claim_next("connection", "system")
        assert item is not None and item.claim_token is not None
        return item


@pytest.fixture
async def work(tmp_path: Path) -> AsyncIterator[Work]:
    database = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'turns.db'}")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(database)
    engine = RecordingEngine()
    runs = SqlRunRepository(sessions)
    service = ConversationService(
        engine, SqlRepository(sessions), run_repository=runs, system_name="system"
    )
    try:
        yield Work(SqlChannelRepository(sessions), service, engine, runs, Identities(), database)
    finally:
        await service.close()
        await database.dispose()


async def test_completed_response_lookup_checks_owner_and_preserves_the_original_answer(work):
    conversation = await work.service.create(ALICE)
    turn = await work.service.prepare_turn(conversation, "question", ALICE)
    assert await work.service.get_turn_response(conversation, turn.run_id, ALICE) is None
    await work.service.complete_turn(
        turn, RunResult(system_name="system", visited=[], answer="stored answer")
    )
    response = await work.service.get_turn_response(conversation, turn.run_id, ALICE)
    assert response is not None and response.content == "stored answer"
    assert response.parent_message_id == turn.message_id
    with pytest.raises(ConversationAccessDenied):
        await work.service.get_turn_response(
            conversation, turn.run_id, Principal.external("other", organization_id="org")
        )


async def test_active_scan_is_bounded_ordered_and_skips_review(work):
    first = await work.claim()
    second = await work.claim("two", sender="another")
    await work.repository.finish(first.event_id, first.claim_token, InboxState.NEEDS_REVIEW)
    page = await work.repository.list_active("connection", "system", limit=1)
    assert [item.event_id for item in page] == [second.event_id]
    assert (
        await work.repository.list_active("connection", "system", after_sequence=second.sequence)
        == ()
    )
    assert await work.repository.list_active("other", "system") == ()


async def test_processing_executes_once_then_stages_the_persisted_response(work):
    item = await work.claim()
    await work.processor().process(item)
    await work.processor().process(item)
    assert work.engine.prompts == ["hello"]
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.ANSWERED
    reply = (await work.repository.list_replies(item.event_id))[0]
    assert reply.text == "answer:hello"
    assert work.identities.calls >= 2


async def test_identity_is_refreshed_after_preparation_and_credentials_stay_request_scoped(work):
    current = replace(
        ALICE, roles=("current-permission",), access_token="test-only-current-credential"
    )
    work.identities = Identities(ALICE, current)
    await work.processor().process(await work.claim())
    context = work.engine.contexts[0]
    assert context.auth_context.roles == ("current-permission",)
    assert context.auth_context.inbound_access_token == "test-only-current-credential"


@pytest.mark.parametrize(
    "principal",
    [None, Principal.anonymous("visitor"), Principal.external("alice", organization_id="other")],
)
async def test_revocation_before_execution_blocks_prepared_work(work, principal):
    work.identities = Identities(ALICE, principal)
    item = await work.claim()
    await work.processor().process(item)
    assert work.engine.prompts == []
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW
    assert await work.repository.list_replies(item.event_id) == ()


async def test_prepared_receipt_recovers_without_adding_another_user_message(work):
    item = await work.claim()
    prepared = await work.preparer().prepare(item.event_id, item.claim_token)
    await work.processor().process(await work.repository.get_inbound(item.event_id))
    history = await work.service.history(prepared.session_id, ALICE)
    assert [(message.role.value, message.content) for message in history] == [
        ("user", "hello"),
        ("assistant", "answer:hello"),
    ]
    assert work.engine.prompts == ["hello"]


@pytest.mark.parametrize(
    "status", [RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED]
)
async def test_running_without_stored_answer_is_never_reexecuted(work, status):
    item = await work.claim()
    await work.preparer().prepare(item.event_id, item.claim_token)
    assert await work.repository.claim_execution(item.event_id, item.claim_token)
    if status is not RunStatus.RUNNING:
        await work.runs.transition_if_allowed(
            (await work.repository.get_inbound(item.event_id)).run_id, status
        )
    await work.processor().process(await work.repository.get_inbound(item.event_id))
    assert work.engine.prompts == []
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW


async def test_saved_answer_is_recovered_after_staging_failure_without_regeneration(
    work, monkeypatch
):
    item = await work.claim()
    original = work.repository.stage_reply

    async def unavailable(*args, **kwargs):
        raise OSError("private-storage-credential")

    monkeypatch.setattr(work.repository, "stage_reply", unavailable)
    await work.processor().process(item)
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.RUNNING
    monkeypatch.setattr(work.repository, "stage_reply", original)
    await work.processor().process(await work.repository.get_inbound(item.event_id))
    assert work.engine.prompts == ["hello"]
    assert (await work.repository.list_replies(item.event_id))[0].text == "answer:hello"


async def test_recovery_of_internal_answer_still_requires_the_original_account_link(work):
    item = await work.claim()
    turn = await work.preparer().prepare(item.event_id, item.claim_token)
    assert await work.repository.claim_execution(item.event_id, item.claim_token)
    await work.service.complete_turn(
        turn, RunResult(system_name="system", visited=[], answer="internal answer")
    )
    work.identities = Identities(None)
    await work.processor().process(await work.repository.get_inbound(item.event_id))
    assert await work.repository.list_replies(item.event_id) == ()
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW


class Ownership(ChannelWorkerOwnership):
    lost = False

    @asynccontextmanager
    async def hold(self):
        yield

    async def check(self):
        if self.lost:
            raise ChannelWorkerOwnershipLost("test ownership lost")


async def wait_state(work, event_id, state):
    async with asyncio.timeout(2):
        while (await work.repository.get_inbound(event_id)).state is not state:
            await asyncio.sleep(0.005)


async def test_scheduler_keeps_sender_order_while_an_independent_sender_progresses(
    work, monkeypatch
):
    from agent_manager.application.channels.worker import ChannelWorker

    first = await work.claim(content="blocked")
    second = await work.repository.ingest(InboundMessage(ADDRESS, "two", "next", NOW, NOW))
    another = await work.repository.ingest(
        InboundMessage(replace(ADDRESS, sender_id="other"), "three", "independent", NOW, NOW)
    )
    started, release = asyncio.Event(), asyncio.Event()
    original = work.engine.stream

    async def stream(message, **kwargs):
        if message == "blocked":
            started.set()
            await release.wait()
        async for event in original(message, **kwargs):
            yield event

    monkeypatch.setattr(work.engine, "stream", stream)
    async with ChannelWorker(work.repository, work.processor(), Ownership(), OPTIONS):
        await asyncio.wait_for(started.wait(), 2)
        await wait_state(work, another.event_id, InboxState.ANSWERED)
        assert (await work.repository.get_inbound(second.event_id)).state is InboxState.QUEUED
        release.set()
        await wait_state(work, first.event_id, InboxState.ANSWERED)
        await wait_state(work, second.event_id, InboxState.ANSWERED)
    assert work.engine.prompts.count("blocked") == work.engine.prompts.count("next") == 1


async def test_shutdown_cancels_and_awaits_active_stream_and_blocks_reexecution(work, monkeypatch):
    from agent_manager.application.channels.worker import ChannelWorker

    item = await work.claim()
    started, closed = asyncio.Event(), asyncio.Event()

    async def stream(message, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
            yield
        finally:
            closed.set()

    monkeypatch.setattr(work.engine, "stream", stream)
    options = replace(OPTIONS, drain_seconds=0.02)
    async with ChannelWorker(work.repository, work.processor(), Ownership(), options):
        await asyncio.wait_for(started.wait(), 2)
    assert closed.is_set()
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW
    assert await work.repository.claim_next("connection", "system") is None


async def test_ownership_loss_aborts_active_execution_instead_of_continuing(work, monkeypatch):
    from agent_manager.application.channels.worker import ChannelWorker

    item = await work.claim()
    started, closed = asyncio.Event(), asyncio.Event()

    async def stream(message, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
            yield
        finally:
            closed.set()

    monkeypatch.setattr(work.engine, "stream", stream)
    ownership = Ownership()
    with pytest.raises(ChannelWorkerOwnershipLost):
        async with ChannelWorker(work.repository, work.processor(), ownership, OPTIONS):
            await asyncio.wait_for(started.wait(), 2)
            ownership.lost = True
            await asyncio.wait_for(closed.wait(), 2)
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW


async def test_ownership_loss_reports_unhealthy_while_engine_cleanup_is_blocked(work, monkeypatch):
    from agent_manager.application.channels.worker import ChannelWorker

    started, closing, release = (asyncio.Event() for _ in range(3))

    async def stream(message, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
            yield
        finally:
            closing.set()
            await release.wait()

    monkeypatch.setattr(work.engine, "stream", stream)
    await work.claim()
    ownership = Ownership()
    worker = ChannelWorker(work.repository, work.processor(), ownership, OPTIONS)
    await worker.__aenter__()
    try:
        await asyncio.wait_for(started.wait(), 2)
        ownership.lost = True
        await asyncio.wait_for(closing.wait(), 2)
        assert not worker.running
    finally:
        release.set()
        with pytest.raises(ChannelWorkerOwnershipLost):
            await worker.__aexit__(None, None, None)


class ApprovalEngine(RecordingEngine):
    def __init__(self, runs, *, confirmation="confirmed", closing=None, release=None):
        super().__init__()
        self.runs = runs
        self.confirmation = confirmation
        self.closing = closing
        self.release = release
        self.cancelled = []
        self.executed = False

    async def stream(self, message, *, context=None, **kwargs):
        self.prompts.append(message)
        await self.runs.transition_if_allowed(context.run_id, RunStatus.PENDING_APPROVAL)
        try:
            yield RunStreamEvent(
                type="pending_approval",
                run_id=context.run_id,
                approval_id="approval",
                arguments={"token": "private-tool-credential"},
            )
            self.executed = True
            yield RunStreamEvent(type="final", content="unexpected execution")
        finally:
            if self.closing is not None:
                self.closing.set()
            if self.release is not None:
                await self.release.wait()

    async def cancel_pending_approval(
        self, run_id, approval_id, *, caller_user_id=None, caller_session_id=None
    ):
        self.cancelled.append((run_id, approval_id, caller_user_id, caller_session_id))
        if self.confirmation == "error":
            raise RuntimeError("private-cancellation-credential")
        if self.confirmation == "confirmed":
            await self.runs.transition_if_allowed(run_id, RunStatus.CANCELLED)


async def use_engine(work, engine):
    await work.service.close()
    work.engine = engine
    work.service = ConversationService(
        engine,
        SqlRepository(session_factory(work.database)),
        run_repository=work.runs,
        system_name="system",
    )


@pytest.mark.parametrize("confirmation", ["confirmed", "unconfirmed", "error"])
async def test_pending_approval_is_cancelled_or_held_for_review_never_approved(
    work, confirmation, caplog
):
    engine = ApprovalEngine(work.runs, confirmation=confirmation)
    await use_engine(work, engine)
    item = await work.claim()
    await work.processor().process(item)
    stored = await work.repository.get_inbound(item.event_id)
    assert stored.state is (
        InboxState.FAILED if confirmation == "confirmed" else InboxState.NEEDS_REVIEW
    )
    assert engine.executed is False
    assert engine.cancelled == [(stored.run_id, "approval", ALICE.user_id, stored.conversation_id)]
    assert await work.repository.list_replies(item.event_id) == ()
    assert "private-cancellation-credential" not in caplog.text
    assert "private-tool-credential" not in caplog.text


async def test_approval_cancellation_keeps_sender_owned_until_stream_cleanup_finishes(work):
    closing, release = asyncio.Event(), asyncio.Event()
    engine = ApprovalEngine(work.runs, closing=closing, release=release)
    await use_engine(work, engine)
    first = await work.claim()
    await work.repository.ingest(InboundMessage(ADDRESS, "two", "next", NOW, NOW))
    task = asyncio.create_task(work.processor().process(first))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        assert await work.repository.claim_next("connection", "system") is None
    finally:
        release.set()
        await task
    assert await work.repository.claim_next("connection", "system") is not None


async def test_stream_without_final_answer_records_a_failed_run_and_review(work, monkeypatch):
    async def stream(message, **kwargs):
        yield RunStreamEvent(type="answer_delta", content="unfinished")

    monkeypatch.setattr(work.engine, "stream", stream)
    item = await work.claim()
    await work.processor().process(item)
    stored = await work.repository.get_inbound(item.event_id)
    assert stored.state is InboxState.NEEDS_REVIEW
    assert (await work.runs.get(stored.run_id)).status is RunStatus.FAILED


async def test_safe_preparation_retries_its_receipt_after_queue_bookkeeping_failure(
    work, monkeypatch
):
    item = await work.claim()
    original = work.repository.record_prepared

    async def unavailable(*args, **kwargs):
        raise OSError("injected bookkeeping failure")

    monkeypatch.setattr(work.repository, "record_prepared", unavailable)
    await work.processor().process(item)
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.PREPARING
    monkeypatch.setattr(work.repository, "record_prepared", original)
    await work.processor().process(item)
    binding = await work.repository.get_binding(ADDRESS)
    assert len(await work.service.history(binding.conversation_id, ALICE)) == 2
    assert work.engine.prompts == ["hello"]


async def test_reopened_manager_recovers_a_saved_answer_without_starting_the_engine(work):
    item = await work.claim()
    turn = await work.preparer().prepare(item.event_id, item.claim_token)
    assert await work.repository.claim_execution(item.event_id, item.claim_token)
    await work.service.complete_turn(
        turn, RunResult(system_name="system", visited=[], answer="committed before crash")
    )
    await work.service.close()
    url = work.database.url.render_as_string(hide_password=False)
    await work.database.dispose()
    database = create_db_engine(url)
    sessions = session_factory(database)
    engine = RecordingEngine()
    runs = SqlRunRepository(sessions)
    service = ConversationService(
        engine, SqlRepository(sessions), run_repository=runs, system_name="system"
    )
    reopened = Work(SqlChannelRepository(sessions), service, engine, runs, Identities(), database)
    try:
        recovered = await reopened.repository.get_inbound(item.event_id)
        await reopened.processor().process(recovered)
        original_reply = await reopened.repository.list_replies(item.event_id)
        await reopened.processor().process(recovered)
        assert await reopened.repository.list_replies(item.event_id) == original_reply
        assert original_reply[0].text == "committed before crash"
        assert engine.prompts == []
    finally:
        await service.close()
        await database.dispose()


@pytest.mark.parametrize("content", ["", "x" * 65_537], ids=["empty", "oversized"])
async def test_unusable_answer_requires_review_and_is_never_truncated(work, monkeypatch, content):
    async def stream(message, **kwargs):
        yield RunStreamEvent(type="final", content=content)

    monkeypatch.setattr(work.engine, "stream", stream)
    item = await work.claim()
    await work.processor().process(item)
    stored = await work.repository.get_inbound(item.event_id)
    assert stored.state is InboxState.NEEDS_REVIEW
    assert await work.repository.list_replies(item.event_id) == ()
    response = await work.service.get_turn_response(stored.conversation_id, stored.run_id, ALICE)
    assert response.content == content


async def test_late_engine_error_recovers_the_persisted_answer_without_leaking_error_text(
    work, monkeypatch, caplog
):
    original = work.engine.stream

    async def stream(message, **kwargs):
        async for event in original(message, **kwargs):
            yield event
        raise OSError("private-engine-credential")

    monkeypatch.setattr(work.engine, "stream", stream)
    item = await work.claim()
    await work.processor().process(item)
    assert work.engine.prompts == ["hello"]
    assert (await work.repository.list_replies(item.event_id))[0].text == "answer:hello"
    assert "private-engine-credential" not in caplog.text


async def test_timeout_closes_the_engine_and_prevents_execution_retry(work, monkeypatch):
    closed = asyncio.Event()

    async def stream(message, **kwargs):
        try:
            yield RunStreamEvent(type="answer_delta", content="partial")
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(work.engine, "stream", stream)
    item = await work.claim()
    await work.preparer().prepare(item.event_id, item.claim_token)
    await work.processor(replace(OPTIONS, turn_seconds=1)).process(item)
    stored = await work.repository.get_inbound(item.event_id)
    assert stored.state is InboxState.NEEDS_REVIEW and closed.is_set()
    assert (await work.runs.get(stored.run_id)).status is RunStatus.CANCELLED


async def test_unsupported_media_does_no_agent_work_and_does_not_block_next_text(work):
    item = await work.claim(content="", kind="unsupported")
    await work.processor().process(item)
    assert work.engine.prompts == []
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.FAILED
    next_item = await work.claim("next", content="next text")
    await work.processor().process(next_item)
    assert work.engine.prompts == ["next text"]


async def test_shutdown_waits_for_slow_engine_cleanup_before_releasing_ownership(work, monkeypatch):
    from agent_manager.application.channels.worker import ChannelWorker

    started, closing, release, closed = (asyncio.Event() for _ in range(4))

    async def stream(message, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
            yield
        finally:
            closing.set()
            await release.wait()
            closed.set()

    monkeypatch.setattr(work.engine, "stream", stream)
    await work.claim()
    worker = ChannelWorker(
        work.repository, work.processor(), Ownership(), replace(OPTIONS, drain_seconds=0.01)
    )
    await worker.__aenter__()
    await asyncio.wait_for(started.wait(), 2)
    stopped = asyncio.create_task(worker.__aexit__(None, None, None))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(stopped), 1.2)
        assert not closed.is_set()
    finally:
        release.set()
        await stopped
    assert closed.is_set()


async def test_shutdown_does_not_start_a_claim_that_returns_after_stopping(work, monkeypatch):
    from agent_manager.application.channels.worker import ChannelWorker

    item = await work.repository.ingest(InboundMessage(ADDRESS, "delayed", "queued", NOW, NOW))
    claimed, release = asyncio.Event(), asyncio.Event()
    original = work.repository.claim_next

    async def delayed_claim(*args, **kwargs):
        result = await original(*args, **kwargs)
        if result is not None:
            claimed.set()
            await release.wait()
        return result

    monkeypatch.setattr(work.repository, "claim_next", delayed_claim)
    worker = ChannelWorker(work.repository, work.processor(), Ownership(), OPTIONS)
    await worker.__aenter__()
    await asyncio.wait_for(claimed.wait(), 2)
    stopped = asyncio.create_task(worker.__aexit__(None, None, None))
    try:
        async with asyncio.timeout(2):
            while worker.running:
                await asyncio.sleep(0)
    finally:
        release.set()
        await stopped
    assert work.engine.prompts == []
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.PREPARING
    monkeypatch.setattr(work.repository, "claim_next", original)
    async with ChannelWorker(work.repository, work.processor(), Ownership(), OPTIONS):
        await wait_state(work, item.event_id, InboxState.ANSWERED)
    assert work.engine.prompts == ["queued"]


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_shutdown_retains_real_ownership_until_stream_cleanup(
    work, monkeypatch, cancellations
):
    from agent_manager.application.channels.worker import ChannelWorker
    from agent_manager.domain.channels.ownership import ChannelWorkerAlreadyRunning
    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    started, closing, release = (asyncio.Event() for _ in range(3))

    async def stream(message, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
            yield
        finally:
            closing.set()
            await release.wait()

    monkeypatch.setattr(work.engine, "stream", stream)
    item = await work.claim()
    url = str(work.database.url)
    worker = ChannelWorker(
        work.repository,
        work.processor(),
        SqlChannelWorkerOwnership(url, "connection"),
        replace(OPTIONS, drain_seconds=0.01),
    )
    await worker.__aenter__()
    await asyncio.wait_for(started.wait(), 2)
    stopped = asyncio.create_task(worker.__aexit__(None, None, None))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        for _ in range(cancellations):
            stopped.cancel()
            await asyncio.wait((stopped,), timeout=0.02)
        with pytest.raises(ChannelWorkerAlreadyRunning):
            async with SqlChannelWorkerOwnership(url, "connection").hold():
                pytest.fail("ownership released while old engine cleanup was still active")
        assert not stopped.done()
    finally:
        release.set()
        await asyncio.gather(stopped, return_exceptions=True)
        # Also join the public shutdown path if the regression detached it.
        await worker.__aexit__(None, None, None)
    assert stopped.cancelled()
    assert (await work.repository.get_inbound(item.event_id)).state is InboxState.NEEDS_REVIEW
    async with SqlChannelWorkerOwnership(url, "connection").hold():
        pass
