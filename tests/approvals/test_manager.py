"""ToolExecutionManager idempotency and ApprovalManager lifecycle/validation.

Neither manager performs risk classification — that concern lives in the
coordinator. These tests cover the resume lifecycle: pending creation, the atomic
claim, authorization, finalization, and execution deduplication.
"""

from __future__ import annotations

import pytest

from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalRunMismatch,
    RunNotFound,
    UnauthorizedApprover,
)
from agent_engine.approvals.manager import (
    ApprovalManager,
    ToolExecutionManager,
    execution_id_for,
)
from agent_engine.approvals.models import ApprovalStatus, RunRecord, RunStatus
from agent_engine.approvals.repository import (
    InMemoryApprovalRepository,
    InMemoryRunRepository,
    InMemoryToolExecutionRepository,
)


def _manager() -> ToolExecutionManager:
    return ToolExecutionManager(execution_repository=InMemoryToolExecutionRepository())


async def test_idempotency_reports_prior_success() -> None:
    mgr = _manager()
    exec_id = execution_id_for("tc1")
    assert await mgr.already_executed(exec_id) is None
    assert (
        await mgr.begin_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t") is True
    )
    # A second begin for the same key is a duplicate.
    assert (
        await mgr.begin_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t") is False
    )
    await mgr.finish_execution(exec_id, status="succeeded", result="R")
    prior = await mgr.already_executed(exec_id)
    assert prior is not None and prior.result == "R"


async def test_idempotency_no_repository_never_dedupes() -> None:
    mgr = ToolExecutionManager()  # no repository
    exec_id = execution_id_for("tc1")
    assert await mgr.already_executed(exec_id) is None
    assert (
        await mgr.begin_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t") is True
    )


# ------------------------------- ApprovalManager ------------------------------ #


def _approval_manager() -> ApprovalManager:
    return ApprovalManager(
        run_repository=InMemoryRunRepository(),
        approval_repository=InMemoryApprovalRepository(),
    )


async def _pending(mgr: ApprovalManager, *, user: str | None = None):
    return await mgr.create_pending(
        run_id="r1",
        thread_id="r1",
        approval_id="ap1",
        agent_id="a",
        tool_name="send_email",
        tool_call_id="tc1",
        provider="local",
        description="agent 'a' wants to call 'send_email'",
        arguments={"to": "x@y.com", "api_key": "secret"},
        authorized_user_id=user,
    )


async def test_create_pending_sets_run_pending_and_masks() -> None:
    mgr = _approval_manager()
    record = await _pending(mgr)
    assert record.status == ApprovalStatus.PENDING
    assert record.arguments["api_key"] == "***redacted***"
    assert record.arguments["to"] == "x@y.com"
    run = await mgr.get_run("r1")
    assert run.status == RunStatus.PENDING_APPROVAL


async def test_create_pending_is_idempotent_by_tool_call() -> None:
    mgr = _approval_manager()
    first = await _pending(mgr)
    second = await _pending(mgr)
    assert first.approval_id == second.approval_id


async def test_claim_moves_run_and_approval_to_resuming() -> None:
    mgr = _approval_manager()
    await _pending(mgr)
    claimed = await mgr.claim(run_id="r1", approval_id="ap1")
    assert claimed.status == ApprovalStatus.RESUMING
    assert (await mgr.get_run("r1")).status == RunStatus.RESUMING


async def test_second_claim_is_rejected() -> None:
    mgr = _approval_manager()
    await _pending(mgr)
    await mgr.claim(run_id="r1", approval_id="ap1")
    with pytest.raises(ApprovalAlreadyProcessed):
        await mgr.claim(run_id="r1", approval_id="ap1")


async def test_claim_validates_run_membership() -> None:
    mgr = _approval_manager()
    await _pending(mgr)
    await mgr.register_run(RunRecord(run_id="r2", thread_id="r2", system_name="s"))
    with pytest.raises(ApprovalRunMismatch):
        await mgr.claim(run_id="r2", approval_id="ap1")


async def test_claim_unknown_run_raises() -> None:
    mgr = _approval_manager()
    await _pending(mgr)
    with pytest.raises(RunNotFound):
        await mgr.claim(run_id="ghost", approval_id="ap1")


async def test_unauthorized_approver_rejected() -> None:
    mgr = _approval_manager()
    await _pending(mgr, user="owner")
    with pytest.raises(UnauthorizedApprover):
        await mgr.claim(run_id="r1", approval_id="ap1", caller_user_id="intruder")


async def test_authorized_approver_allowed() -> None:
    mgr = _approval_manager()
    await _pending(mgr, user="owner")
    claimed = await mgr.claim(run_id="r1", approval_id="ap1", caller_user_id="owner")
    assert claimed.status == ApprovalStatus.RESUMING


async def test_finalize_rejects() -> None:
    mgr = _approval_manager()
    await _pending(mgr)
    await mgr.claim(run_id="r1", approval_id="ap1")
    rec = await mgr.finalize("ap1", approved=False)
    assert rec.status == ApprovalStatus.REJECTED


def test_execution_id_is_stable_and_scoped() -> None:
    assert execution_id_for("tc1") == execution_id_for("tc1")
    assert execution_id_for("tc1") != execution_id_for("tc2")
    assert execution_id_for("tc1", salt="ap1") != execution_id_for("tc1")
