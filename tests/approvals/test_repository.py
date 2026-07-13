"""Repository semantics: atomic claim, idempotency, lookups."""

from __future__ import annotations

import asyncio

import pytest

from agent_engine.approvals.errors import ApprovalNotFound, InvalidStateTransition
from agent_engine.approvals.models import (
    ApprovalRecord,
    ApprovalStatus,
    ToolExecutionRecord,
)
from agent_engine.approvals.repository import (
    InMemoryApprovalRepository,
    InMemoryToolExecutionRepository,
)

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
    assert first is second


async def test_execution_complete_records_result() -> None:
    repo = InMemoryToolExecutionRepository()
    await repo.start(
        ToolExecutionRecord(execution_id="e1", tool_call_id="tc1", run_id="r1", tool_name="t")
    )
    await repo.complete("e1", status="succeeded", result="done")
    rec = await repo.get("e1")
    assert rec is not None and rec.status == "succeeded" and rec.result == "done"
