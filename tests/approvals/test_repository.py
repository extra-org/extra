"""Repository semantics: atomic claim, idempotency, lookups."""

from __future__ import annotations

import asyncio

import pytest

from agent_engine.approvals.errors import ApprovalNotFound, InvalidStateTransition
from agent_engine.approvals.in_memory_approval_repository import InMemoryApprovalRepository
from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.models import (
    ApprovalRecord,
    ApprovalStatus,
    ToolExecutionRecord,
    ToolExecutionStatus,
)
from agent_engine.runtime.tool_results import NormalizedToolResult

pytestmark = pytest.mark.asyncio


def _approval(approval_id: str = "ap1", tool_call_id: str = "tc1") -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        run_id="r1",
        thread_id="r1",
        agent_id="a",
        tool_name="delete_user",
        tool_call_id=tool_call_id,
        provider="local",
        description="wants to call delete_user",
        arguments={},
    )


async def test_claim_is_atomic_single_winner() -> None:
    repo = InMemoryApprovalRepository()
    await repo.create(_approval())

    async def claim() -> bool:
        try:
            await repo.claim("ap1")
            return True
        except InvalidStateTransition:
            return False

    results = await asyncio.gather(*[claim() for _ in range(25)])
    assert sum(results) == 1  # exactly one caller wins the PENDING -> RESUMING move


async def test_claim_and_cancel_have_exactly_one_winner() -> None:
    repo = InMemoryApprovalRepository()
    await repo.create(_approval())

    async def transition(operation: str) -> str | None:
        try:
            if operation == "claim":
                await repo.claim("ap1")
            else:
                await repo.reject_pending("ap1")
            return operation
        except InvalidStateTransition:
            return None

    winners = await asyncio.gather(transition("claim"), transition("cancel"))
    assert len([winner for winner in winners if winner is not None]) == 1


async def test_claim_missing_raises() -> None:
    repo = InMemoryApprovalRepository()
    with pytest.raises(ApprovalNotFound):
        await repo.claim("nope")


async def test_get_by_tool_call() -> None:
    repo = InMemoryApprovalRepository()
    await repo.create(_approval(tool_call_id="tcX"))
    found = await repo.get_by_tool_call("r1", "tcX")
    assert found is not None and found.approval_id == "ap1"
    assert await repo.get_by_tool_call("r1", "other") is None


async def test_pending_for_run_only_returns_pending() -> None:
    repo = InMemoryApprovalRepository()
    await repo.create(_approval())
    pending = await repo.get_pending_for_run("r1")
    assert pending is not None and pending.approval_id == "ap1"
    await repo.set_status("ap1", ApprovalStatus.REJECTED)
    assert await repo.get_pending_for_run("r1") is None


async def test_execution_idempotency_start_is_create_if_absent() -> None:
    repo = InMemoryToolExecutionRepository()
    rec = ToolExecutionRecord(execution_id="e1", tool_call_id="tc1", run_id="r1", tool_name="t")
    first, created1 = await repo.start(rec)
    second, created2 = await repo.start(rec)
    assert created1 is True
    assert created2 is False  # duplicate attempt detected
    assert first == second
    assert first is not second  # callers cannot mutate the repository's record


async def test_execution_complete_records_result() -> None:
    repo = InMemoryToolExecutionRepository()
    await repo.start(
        ToolExecutionRecord(execution_id="e1", tool_call_id="tc1", run_id="r1", tool_name="t")
    )
    result = NormalizedToolResult(text="done", structured={"ok": True})
    await repo.complete(
        "e1",
        status=ToolExecutionStatus.SUCCEEDED,
        result=result.to_persisted(),
    )
    rec = await repo.get("e1")
    assert rec is not None and rec.status == ToolExecutionStatus.SUCCEEDED
    assert rec.result == result.to_persisted()

    with pytest.raises(ValueError, match="already terminal"):
        await repo.complete(
            "e1",
            status=ToolExecutionStatus.FAILED,
            result=NormalizedToolResult.text_only("replacement").to_persisted(),
        )

    unchanged = await repo.get("e1")
    assert unchanged == rec


async def test_execution_waiter_receives_the_terminal_snapshot() -> None:
    repo = InMemoryToolExecutionRepository()
    await repo.start(
        ToolExecutionRecord(execution_id="e1", tool_call_id="tc1", run_id="r1", tool_name="t")
    )
    waiter = asyncio.create_task(repo.wait_for_completion("e1"))
    await asyncio.sleep(0)
    assert waiter.done() is False

    result = NormalizedToolResult("done", structured={"ok": True}).to_persisted()
    await repo.complete("e1", status=ToolExecutionStatus.SUCCEEDED, result=result)

    completed = await waiter
    assert completed.status == ToolExecutionStatus.SUCCEEDED
    assert completed.result == result
