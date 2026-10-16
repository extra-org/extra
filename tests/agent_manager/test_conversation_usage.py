"""Conversation budgets include work without a final assistant message."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import ToolCall
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.errors import ApprovalError
from agent_engine.approvals.models import RunRecord, RunStatus
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runs.repository import RunRepository
from agent_engine.runtime.hooks import RunContext
from agent_manager.application import ConversationService, ConversationTokenBudgetExceeded
from agent_manager.domain import Principal, Repository, Role
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.run_repository import SqlRunRepository
from agent_manager.infrastructure.persistence.sql_repository import (
    SqlRepository,
    run_token_usage_upsert,
)
from tests.agent_manager.conftest import bearer, build_test_app
from tests.engine.test_stream_lifecycle import AnsweringModel, BlockingModel, _spec, _write_tool

ALICE = Principal.external("alice")


@pytest.fixture(params=["memory", "sql"])
async def repositories(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[Repository, RunRepository]]:
    if request.param == "memory":
        yield MemoryRepository(), InMemoryRunRepository()
    else:
        database: AsyncEngine = create_db_engine("sqlite+aiosqlite:///:memory:")
        async with database.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        sessions = session_factory(database)
        try:
            yield SqlRepository(sessions), SqlRunRepository(sessions)
        finally:
            await database.dispose()


@pytest.mark.parametrize("cancel_task", [False, True], ids=["close", "task-cancel"])
async def test_cancelled_usage_counts_toward_conversation_budget(
    tmp_path: Path, repositories: tuple[Repository, RunRepository], cancel_task: bool
) -> None:
    repository, runs = repositories
    model = BlockingModel()

    def factory(*args: Any, **kwargs: Any) -> Any:
        return model

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec())
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=5)
        cid = await service.create(ALICE)
        events = service.stream(cid, "hello", ALICE)
        if cancel_task:
            received = asyncio.Event()

            async def consume() -> None:
                async for event in events:
                    if event.type == "answer_delta":
                        received.set()

            task = asyncio.create_task(consume())
            await asyncio.wait_for(received.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            async for event in events:
                if event.type == "answer_delta":
                    break
            assert isinstance(events, AsyncGenerator)
            await events.aclose()

        history = await service.history(cid, ALICE)
        assert len(history) == 1
        assert history[0].status == "cancelled"
        assert history[0].run_id is not None
        run = await runs.get(history[0].run_id)
        assert run is not None
        assert (run.input_tokens, run.output_tokens) == (4, 1)
        assert (await service.usage(cid, ALICE)).used_tokens == 5
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "another request", ALICE)


class BlockingAfterUsageModel:
    """Finish a charged model step, then block on the following model call."""

    def __init__(self) -> None:
        self.blocked = asyncio.Event()

    def bind_tools(self, tools: list[Any]) -> BlockingAfterUsageModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            self.blocked.set()
            await asyncio.Event().wait()
        return AIMessage(
            content="",
            tool_calls=[ToolCall(name="missing_tool", args={}, id="first")],
            usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
        )


async def test_nonstream_cancellation_keeps_usage_from_completed_model_steps(
    tmp_path: Path, repositories: tuple[Repository, RunRepository]
) -> None:
    repository, runs = repositories
    model = BlockingAfterUsageModel()

    def factory(*args: Any, **kwargs: Any) -> Any:
        return model

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec())
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=5)
        cid = await service.create(ALICE)
        task = asyncio.create_task(service.send(cid, "hello", ALICE))
        await asyncio.wait_for(model.blocked.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        history = await service.history(cid, ALICE)
        assert len(history) == 1 and history[0].status == "cancelled"
        assert history[0].run_id is not None
        record = await runs.get(history[0].run_id)
        assert record is not None
        assert (record.input_tokens, record.output_tokens) == (4, 1)
        assert (await service.usage(cid, ALICE)).used_tokens == 5
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "next", ALICE)


class DelayedUsageAcknowledgement(InMemoryRunRepository):
    """Model a storage commit followed by a cancellable response/readback."""

    def __init__(self) -> None:
        super().__init__()
        self.hold_next_write = False
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def add_token_usage(
        self, run_id: str, *, input_tokens: int | None, output_tokens: int | None
    ) -> RunRecord | None:
        record = await super().add_token_usage(
            run_id, input_tokens=input_tokens, output_tokens=output_tokens
        )
        if self.hold_next_write:
            self.hold_next_write = False
            self.committed.set()
            await self.release.wait()
        return record


@pytest.mark.parametrize("phase", ["send", "stream", "resume", "stream_resume"])
async def test_cancellation_during_usage_commit_does_not_charge_twice(
    tmp_path: Path, phase: str
) -> None:
    runs = DelayedUsageAcknowledgement()
    repository = MemoryRepository()
    resumed = phase in {"resume", "stream_resume"}
    _write_tool(tmp_path, "send_email")

    def factory(*args: Any, **kwargs: Any) -> Any:
        if resumed:
            return ApprovalUsageModel()
        return AnsweringModel(usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5})

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email" if resumed else None, auto_mode=not resumed))
        expected = 10 if resumed else 5
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=expected)
        cid = await service.create(ALICE)
        pending = (await service.send(cid, "hello", ALICE)).pending_approval if resumed else None

        async def execute() -> None:
            if phase == "send":
                await service.send(cid, "hello", ALICE)
            elif phase == "stream":
                _ = [event async for event in service.stream(cid, "hello", ALICE)]
            else:
                assert pending is not None
                if phase == "resume":
                    await service.decide_approval(
                        cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                    )
                else:
                    events = await service.stream_approval(
                        cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                    )
                    _ = [event async for event in events]

        runs.hold_next_write = True
        task = asyncio.create_task(execute())
        try:
            await asyncio.wait_for(runs.committed.wait(), timeout=1)
            task.cancel()
        finally:
            runs.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

        history = await service.history(cid, ALICE)
        assert len(history) == 1 and history[0].status == "cancelled"
        assert history[0].run_id is not None
        record = await runs.get(history[0].run_id)
        assert record is not None
        assert (record.input_tokens or 0) + (record.output_tokens or 0) == expected
        assert (await service.usage(cid, ALICE)).used_tokens == expected
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "next", ALICE)


class ApprovalUsageModel:
    """Two sequential tool approvals, each model turn reporting five tokens."""

    def __init__(self, *, block_on_resume: bool = False) -> None:
        self.block_on_resume = block_on_resume

    def bind_tools(self, tools: list[Any]) -> ApprovalUsageModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        completed = sum(isinstance(message, ToolMessage) for message in messages)
        calls = (
            [ToolCall(name="send_email", args={"message": str(completed)}, id=f"c{completed}")]
            if completed < 2
            else []
        )
        return AIMessage(
            content="" if calls else "done",
            tool_calls=calls,
            usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
        )

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage | AIMessageChunk]:
        if self.block_on_resume and any(isinstance(message, ToolMessage) for message in messages):
            yield AIMessageChunk(
                content="partial",
                usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            )
            await asyncio.Event().wait()
        else:
            yield await self.ainvoke(messages)


@pytest.mark.parametrize("stream_initial", [False, True])
async def test_pending_usage_is_reported_without_a_shared_run_repository(
    tmp_path: Path, repositories: tuple[Repository, RunRepository], stream_initial: bool
) -> None:
    repository, runs = repositories
    _write_tool(tmp_path, "send_email")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return ApprovalUsageModel()

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=False))
        service = ConversationService(engine, repository, max_tokens=5)
        cid = await service.create(ALICE)
        if stream_initial:
            events = service.stream(cid, "send", ALICE)
            try:
                async for event in events:
                    if event.type == "pending_approval":
                        assert (event.input_tokens, event.output_tokens) == (4, 1)
                        assert (await service.usage(cid, ALICE)).used_tokens == 5
                        break
            finally:
                assert isinstance(events, AsyncGenerator)
                await events.aclose()
        else:
            await service.send(cid, "send", ALICE)

        assert (await service.usage(cid, ALICE)).used_tokens == 5
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "next", ALICE)


@pytest.mark.parametrize("stream_initial", [False, True])
@pytest.mark.parametrize("stream_resume", [False, True])
async def test_pending_and_resumed_usage_is_visible_and_counted_once(
    tmp_path: Path,
    repositories: tuple[Repository, RunRepository],
    stream_initial: bool,
    stream_resume: bool,
) -> None:
    repository, runs = repositories
    model = ApprovalUsageModel()
    _write_tool(tmp_path, "send_email")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return model

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=False))
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=5)
        cid = await service.create(ALICE)
        if stream_initial:
            events = service.stream(cid, "send twice", ALICE)
            async for event in events:
                if event.type == "pending_approval":
                    # Check before closing the stream, as soon as the UI can see it.
                    assert (await service.usage(cid, ALICE)).used_tokens == 5
                    break
            assert isinstance(events, AsyncGenerator)
            await events.aclose()
            pending = await engine.get_pending_approval(event.run_id or "")
        else:
            pending = (await service.send(cid, "send twice", ALICE)).pending_approval
        assert pending is not None
        run_id = pending.run_id
        assert (await service.usage(cid, ALICE)).used_tokens == 5
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "new turn", ALICE)

        for expected_total in (10, 15):
            assert pending is not None
            approval_id = pending.approval_id
            if stream_resume:
                events = await service.stream_approval(
                    cid, run_id, approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                )
                async for event in events:
                    if event.type in {"pending_approval", "final"}:
                        assert (await service.usage(cid, ALICE)).used_tokens == expected_total
                pending = await engine.get_pending_approval(run_id)
            else:
                result = await service.decide_approval(
                    cid, run_id, approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                )
                pending = result.pending_approval
            assert (await service.usage(cid, ALICE)).used_tokens == expected_total
            if expected_total == 10:
                assert pending is not None
            else:
                assert pending is None

        # Retrying the final decision recovers the answer, rather than charging it again.
        await service.decide_approval(cid, run_id, approval_id, ApprovalDecision.ALLOW_ONCE, ALICE)
        assert (await service.usage(cid, ALICE)).used_tokens == 15
        history = await service.history(cid, ALICE)
        assert [message.role for message in history] == [Role.USER, Role.ASSISTANT]
        assert (history[-1].input_tokens, history[-1].output_tokens) == (12, 3)


@pytest.mark.parametrize("stream_initial", [False, True])
async def test_cancelled_pending_approval_keeps_its_charge(
    tmp_path: Path, repositories: tuple[Repository, RunRepository], stream_initial: bool
) -> None:
    repository, runs = repositories
    _write_tool(tmp_path, "send_email")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return ApprovalUsageModel()

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=False))
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=5)
        cid = await service.create(ALICE)
        if stream_initial:
            events = [event async for event in service.stream(cid, "send", ALICE)]
            pending = await engine.get_pending_approval(events[-1].run_id or "")
        else:
            pending = (await service.send(cid, "send", ALICE)).pending_approval
        assert pending is not None

        await service.cancel_pending_approval(cid, pending.run_id, pending.approval_id, ALICE)

        assert await engine.get_run_status(pending.run_id) == RunStatus.CANCELLED
        assert (await service.usage(cid, ALICE)).used_tokens == 5
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "new turn", ALICE)


async def test_cancelled_resume_keeps_usage_from_all_execution_legs(
    tmp_path: Path, repositories: tuple[Repository, RunRepository]
) -> None:
    repository, runs = repositories
    _write_tool(tmp_path, "send_email")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return ApprovalUsageModel(block_on_resume=True)

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=False))
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=10)
        cid = await service.create(ALICE)
        pending = (await service.send(cid, "send", ALICE)).pending_approval
        assert pending is not None
        events = await service.stream_approval(
            cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
        )
        async for event in events:
            if event.type == "answer_delta":
                break
        assert isinstance(events, AsyncGenerator)
        await events.aclose()

        assert await engine.get_run_status(pending.run_id) == RunStatus.CANCELLED
        assert (await service.usage(cid, ALICE)).used_tokens == 10
        assert len(await service.history(cid, ALICE)) == 1
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "new turn", ALICE)


async def test_editing_away_completed_branches_does_not_refund_usage(
    tmp_path: Path, repositories: tuple[Repository, RunRepository]
) -> None:
    repository, runs = repositories

    def factory(*args: Any, **kwargs: Any) -> Any:
        return AnsweringModel(usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5})

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec())
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=15)
        cid = await service.create(ALICE)
        await service.send(cid, "first", ALICE)
        await service.send(cid, "second", ALICE)
        original = (await service.history(cid, ALICE))[0]
        await service.send(cid, "edited first", ALICE, edit_message_id=original.message_id)

        assert len(await service.history(cid, ALICE)) == 2
        assert (await service.usage(cid, ALICE)).used_tokens == 15
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "new turn", ALICE)


class FailingAfterUsageModel:
    """Report five tokens per tool-calling turn, then fail the leg's last call."""

    def bind_tools(self, tools: list[Any]) -> FailingAfterUsageModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        completed = sum(isinstance(message, ToolMessage) for message in messages)
        if completed == 2:
            raise RuntimeError("provider unavailable")
        # The second call names an unknown tool, so it needs no approval.
        name = "send_email" if completed == 0 else "missing_tool"
        return AIMessage(
            content="",
            tool_calls=[ToolCall(name=name, args={"message": "x"}, id=f"c{completed}")],
            usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
        )

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield await self.ainvoke(messages)


@pytest.mark.parametrize("phase", ["send", "stream", "resume", "stream_resume"])
async def test_failed_turn_keeps_usage_reported_before_the_failure(
    tmp_path: Path, repositories: tuple[Repository, RunRepository], phase: str
) -> None:
    repository, runs = repositories
    _write_tool(tmp_path, "send_email")
    resumed = phase in ("resume", "stream_resume")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return FailingAfterUsageModel()

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=not resumed))
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=10)
        cid = await service.create(ALICE)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            if phase == "send":
                await service.send(cid, "send", ALICE)
            elif phase == "stream":
                _ = [event async for event in service.stream(cid, "send", ALICE)]
            else:
                pending = (await service.send(cid, "send", ALICE)).pending_approval
                assert pending is not None
                if phase == "resume":
                    await service.decide_approval(
                        cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                    )
                else:
                    events = await service.stream_approval(
                        cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                    )
                    _ = [event async for event in events]

        assert (await service.usage(cid, ALICE)).used_tokens == 10
        with pytest.raises(ConversationTokenBudgetExceeded):
            await service.prepare_turn(cid, "new turn", ALICE)


@pytest.mark.parametrize("phase", ["send", "stream", "resume", "stream_resume"])
async def test_budget_recovers_a_failed_usage_write_from_the_run_record(
    tmp_path: Path,
    repositories: tuple[Repository, RunRepository],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    repository, runs = repositories
    _write_tool(tmp_path, "send_email")
    resumed = phase in {"resume", "stream_resume"}

    def factory(*args: Any, **kwargs: Any) -> Any:
        return FailingAfterUsageModel()

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OSError("usage ledger unavailable")

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=not resumed))
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=10)
        cid = await service.create(ALICE)
        pending = (await service.send(cid, "send", ALICE)).pending_approval if resumed else None
        with monkeypatch.context() as failure:
            failure.setattr(repository, "record_run_token_usage", unavailable)
            with pytest.raises(RuntimeError, match="provider unavailable"):
                if phase == "send":
                    await service.send(cid, "send", ALICE)
                elif phase == "stream":
                    _ = [event async for event in service.stream(cid, "send", ALICE)]
                else:
                    assert pending is not None
                    if phase == "resume":
                        await service.decide_approval(
                            cid,
                            pending.run_id,
                            pending.approval_id,
                            ApprovalDecision.ALLOW_ONCE,
                            ALICE,
                        )
                    else:
                        events = await service.stream_approval(
                            cid,
                            pending.run_id,
                            pending.approval_id,
                            ApprovalDecision.ALLOW_ONCE,
                            ALICE,
                        )
                        _ = [event async for event in events]

        assert await repository.get_token_usage(cid) == (5 if resumed else 0)
        # No successful usage read or request-local state is needed to enforce it.
        restarted = ConversationService(engine, repository, run_repository=runs, max_tokens=10)
        with monkeypatch.context() as failure:
            failure.setattr(repository, "record_run_token_usage", unavailable)
            with pytest.raises(ConversationTokenBudgetExceeded):
                await restarted.prepare_turn(cid, "another request", ALICE)
            assert (await restarted.usage(cid, ALICE)).used_tokens == 10
        # Once storage recovers, a read retains the charge past run-record expiry.
        assert (await restarted.usage(cid, ALICE)).used_tokens == 10
        assert await repository.get_token_usage(cid) == 10


@pytest.mark.parametrize("streamed", [False, True])
async def test_sql_usage_write_does_not_retry_a_committed_charge_after_a_read_failure(
    tmp_path: Path, streamed: bool
) -> None:
    database = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(database)
    runs = SqlRunRepository(sessions)
    fail_readback = False

    def unavailable_readback(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        nonlocal fail_readback
        if statement.startswith("UPDATE agent_runs SET") and "input_tokens=" in statement:
            fail_readback = True
        elif fail_readback and statement.startswith("SELECT agent_runs."):
            fail_readback = False
            raise OSError("readback unavailable after commit")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return AnsweringModel(usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5})

    try:
        async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
            await engine.build(_spec())
            service = ConversationService(engine, SqlRepository(sessions), run_repository=runs)
            cid = await service.create(ALICE)
            event.listen(database.sync_engine, "before_cursor_execute", unavailable_readback)
            try:
                if streamed:
                    events = [item async for item in service.stream(cid, "hello", ALICE)]
                    assert events[-1].type == "final"
                else:
                    assert (await service.send(cid, "hello", ALICE)).answer == "ok"
            finally:
                event.remove(database.sync_engine, "before_cursor_execute", unavailable_readback)
            assert (await service.usage(cid, ALICE)).used_tokens == 5
    finally:
        await database.dispose()


def test_postgres_usage_upsert_keeps_the_highest_total() -> None:
    # SQLite executes the shared path above; this pins the PostgreSQL rendering.
    statement = run_token_usage_upsert("postgresql", "conv", "run", 7)
    sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert sql.endswith(
        "ON CONFLICT (session_id, run_id) DO UPDATE SET total_tokens = "
        "CASE WHEN (conversation_run_token_usage.total_tokens < 7) THEN 7 "
        "ELSE conversation_run_token_usage.total_tokens END"
    )


class UnavailableUsageRepository(MemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.usage_writes_fail = False

    async def record_run_token_usage(
        self, conversation_id: str, run_id: str, total_tokens: int
    ) -> None:
        if self.usage_writes_fail:
            raise OSError("usage ledger unavailable")
        await super().record_run_token_usage(conversation_id, run_id, total_tokens)


@pytest.mark.parametrize("phase", ["send", "stream", "resume", "stream_resume", "cancel_again"])
async def test_usage_retention_failure_does_not_replace_the_original_error(
    tmp_path: Path, phase: str
) -> None:
    repository = UnavailableUsageRepository()
    runs = InMemoryRunRepository()
    _write_tool(tmp_path, "send_email")
    initial = phase in ("send", "stream")

    def factory(*args: Any, **kwargs: Any) -> Any:
        return FailingAfterUsageModel()

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=initial))
        service = ConversationService(engine, repository, run_repository=runs)
        cid = await service.create(ALICE)
        if initial:
            repository.usage_writes_fail = True
            with pytest.raises(RuntimeError, match="provider unavailable"):
                if phase == "send":
                    await service.send(cid, "send", ALICE)
                else:
                    _ = [event async for event in service.stream(cid, "send", ALICE)]
            return

        pending = (await service.send(cid, "send", ALICE)).pending_approval
        assert pending is not None
        if phase == "cancel_again":
            await service.cancel_pending_approval(cid, pending.run_id, pending.approval_id, ALICE)
            repository.usage_writes_fail = True
            with pytest.raises(ApprovalError):
                await service.cancel_pending_approval(
                    cid, pending.run_id, pending.approval_id, ALICE
                )
            return

        repository.usage_writes_fail = True
        with pytest.raises(RuntimeError, match="provider unavailable"):
            if phase == "resume":
                await service.decide_approval(
                    cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                )
            else:
                events = await service.stream_approval(
                    cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                )
                _ = [event async for event in events]


@pytest.mark.parametrize("phase", ["send", "stream", "resume", "stream_resume"])
async def test_usage_retention_failure_keeps_the_completed_answer_and_its_charge(
    tmp_path: Path, phase: str
) -> None:
    repository = UnavailableUsageRepository()
    runs = InMemoryRunRepository()
    _write_tool(tmp_path, "send_email")
    initial = phase in ("send", "stream")
    model: Any = (
        AnsweringModel(usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5})
        if initial
        else ApprovalUsageModel()
    )

    def factory(*args: Any, **kwargs: Any) -> Any:
        return model

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec() if initial else _spec(tool_id="send_email", auto_mode=False))
        service = ConversationService(engine, repository, run_repository=runs)
        cid = await service.create(ALICE)
        if phase == "send":
            repository.usage_writes_fail = True
            await service.send(cid, "hello", ALICE)
        elif phase == "stream":
            repository.usage_writes_fail = True
            streamed = [event async for event in service.stream(cid, "hello", ALICE)]
            assert streamed[-1].type == "final"
        else:
            pending = (await service.send(cid, "send", ALICE)).pending_approval
            assert pending is not None
            second = await service.decide_approval(
                cid, pending.run_id, pending.approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
            )
            assert second.pending_approval is not None
            approval_id = second.pending_approval.approval_id
            repository.usage_writes_fail = True
            if phase == "resume":
                await service.decide_approval(
                    cid, pending.run_id, approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                )
            else:
                events = await service.stream_approval(
                    cid, pending.run_id, approval_id, ApprovalDecision.ALLOW_ONCE, ALICE
                )
                assert [event async for event in events][-1].type == "final"

        history = await service.history(cid, ALICE)
        assert [message.role for message in history] == [Role.USER, Role.ASSISTANT]
        assert (await service.usage(cid, ALICE)).used_tokens == (5 if initial else 15)


async def test_cancelled_charge_survives_engine_run_expiry(tmp_path: Path) -> None:
    now = 0.0
    runs = InMemoryRunRepository(terminal_ttl_seconds=1, clock=lambda: now)
    repository = MemoryRepository()
    model = BlockingModel()

    def factory(*args: Any, **kwargs: Any) -> Any:
        return model

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec())
        service = ConversationService(engine, repository, run_repository=runs, max_tokens=5)
        cid = await service.create(ALICE)
        events = service.stream(cid, "hello", ALICE)
        async for event in events:
            if event.type == "answer_delta":
                break
        assert isinstance(events, AsyncGenerator)
        await events.aclose()
        history = await service.history(cid, ALICE)
        assert history[0].run_id is not None
        now = 2.0
        assert await runs.get(history[0].run_id) is None

        # No usage request was made while the engine record still existed.
        restarted_service = ConversationService(
            engine, repository, run_repository=runs, max_tokens=5
        )
        assert (await restarted_service.usage(cid, ALICE)).used_tokens == 5
        with pytest.raises(ConversationTokenBudgetExceeded):
            await restarted_service.prepare_turn(cid, "new turn", ALICE)


class LostUsageAcknowledgement(InMemoryRunRepository):
    """Commit one usage write, then lose its acknowledgement."""

    def __init__(self) -> None:
        super().__init__()
        self.lose_next_acknowledgement = True

    async def add_token_usage(
        self, run_id: str, *, input_tokens: int | None, output_tokens: int | None
    ) -> RunRecord | None:
        record = await super().add_token_usage(
            run_id, input_tokens=input_tokens, output_tokens=output_tokens
        )
        if self.lose_next_acknowledgement:
            self.lose_next_acknowledgement = False
            raise ConnectionError("usage commit acknowledgement lost")
        return record


@pytest.mark.parametrize("stream", [False, True])
async def test_retry_after_an_ambiguous_usage_commit_charges_the_leg_once(
    tmp_path: Path, stream: bool
) -> None:
    runs = LostUsageAcknowledgement()
    model = AnsweringModel(usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5})

    def factory(*args: Any, **kwargs: Any) -> Any:
        return model

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec())
        context = RunContext(run_id="ambiguous")
        with pytest.raises(ConnectionError):
            if stream:
                _ = [event async for event in engine.stream("hello", context=context)]
            else:
                await engine.run("hello", context=context)

    run = await runs.get("ambiguous")
    assert run is not None
    assert (run.input_tokens, run.output_tokens) == (4, 1)


async def test_conditional_usage_add_applies_once_per_observed_baseline(
    repositories: tuple[Repository, RunRepository],
) -> None:
    _, runs = repositories
    await runs.create_if_absent(
        RunRecord(run_id="run", thread_id="run", system_name="", status=RunStatus.RUNNING)
    )

    async def add(expected: tuple[int | None, int | None], tokens: int) -> tuple[int | None, ...]:
        record = await runs.add_token_usage_if_unchanged(
            "run", expected=expected, input_tokens=tokens, output_tokens=1
        )
        assert record is not None
        # Read the totals now: the in-memory adapter returns its live record.
        return record.input_tokens, record.output_tokens

    assert await add((None, None), 4) == (4, 1)
    # A retry with the same baseline after a committed attempt adds nothing.
    assert await add((None, None), 4) == (4, 1)
    assert await add((4, 1), 2) == (6, 2)
    assert (
        await runs.add_token_usage_if_unchanged(
            "missing", expected=(None, None), input_tokens=1, output_tokens=1
        )
        is None
    )


def sse_events(text: str, event_type: str) -> list[dict[str, Any]]:
    prefix = f"event: {event_type}\ndata: "
    return [
        json.loads(frame.removeprefix(prefix))
        for frame in text.split("\n\n")
        if frame.startswith(prefix)
    ]


async def test_pending_usage_reaches_clients_from_both_stream_endpoints(tmp_path: Path) -> None:
    _write_tool(tmp_path, "send_email")
    runs = InMemoryRunRepository()

    def factory(*args: Any, **kwargs: Any) -> Any:
        return ApprovalUsageModel()

    async with LangGraphEngine(tmp_path, model_factory=factory, run_repository=runs) as engine:
        await engine.build(_spec(tool_id="send_email", auto_mode=False))
        app = build_test_app(ConversationService(engine, MemoryRepository(), run_repository=runs))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=bearer("user-1")
        ) as client:
            await client.post("/conversations", json={"session_id": "usage-sse"})
            first = await client.post(
                "/conversations/usage-sse/messages/stream", json={"message": "send"}
            )
            [pending] = sse_events(first.text, "pending_approval")
            resumed = await client.post(
                f"/conversations/usage-sse/runs/{pending['run_id']}"
                f"/approvals/{pending['approval_id']}/decision/stream",
                json={"decision": ApprovalDecision.ALLOW_ONCE.value},
            )
            [second] = sse_events(resumed.text, "pending_approval")

    assert (pending["input_tokens"], pending["output_tokens"]) == (4, 1)
    assert (second["input_tokens"], second["output_tokens"]) == (8, 2)
