"""ToolExecutionManager idempotency and ApprovalManager lifecycle/validation.

Neither manager performs risk classification — that concern lives in the
coordinator. These tests cover the resume lifecycle: pending creation, the atomic
claim, authorization, finalization, and execution deduplication.
"""

from __future__ import annotations

import pytest

from agent_engine.approvals.approval_manager import ApprovalManager
from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalRunMismatch,
    RunNotFound,
    UnauthorizedApprover,
)
from agent_engine.approvals.in_memory_approval_repository import InMemoryApprovalRepository
from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.models import ApprovalStatus, RunRecord, RunStatus, ToolExecutionRecord
from agent_engine.approvals.tool_execution_manager import (
    ToolExecutionManager,
    execution_id_for,
)
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runtime.tool_results import NormalizedToolResult


def _manager() -> ToolExecutionManager:
    return ToolExecutionManager(execution_repository=InMemoryToolExecutionRepository())


async def test_idempotency_reports_prior_success() -> None:
    mgr = _manager()
    exec_id = execution_id_for("tc1")
    assert await mgr.restored_result(exec_id) is None
    claim = await mgr.claim_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t")
    assert claim.should_execute is True
    result = NormalizedToolResult(
        text="Found 2 invoices",
        structured={"count": 2},
        artifact={"source": "billing"},
    )
    await mgr.finish_execution(exec_id, status="succeeded", result=result)
    prior = await mgr.restored_result(exec_id)
    assert prior == result
    replay = await mgr.claim_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t")
    assert replay.should_execute is False
    assert replay.status == "succeeded"
    assert replay.result == result
    assert (
        await mgr.begin_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t") is False
    )


async def test_idempotency_no_repository_never_dedupes() -> None:
    mgr = ToolExecutionManager()  # no repository
    exec_id = execution_id_for("tc1")
    assert await mgr.restored_result(exec_id) is None
    claim = await mgr.claim_execution(exec_id, tool_call_id="tc1", run_id="r1", tool_name="t")
    assert claim.should_execute is True


async def test_legacy_text_result_replays_as_text_only() -> None:
    repository = InMemoryToolExecutionRepository()
    manager = ToolExecutionManager(execution_repository=repository)
    exec_id = execution_id_for("legacy")
    await repository.start(
        ToolExecutionRecord(
            execution_id=exec_id,
            tool_call_id="legacy",
            run_id="r1",
            tool_name="t",
        )
    )
    await repository.complete(exec_id, status="succeeded", result="legacy text")

    assert await manager.restored_result(exec_id) == NormalizedToolResult.text_only("legacy text")


# ------------------------------- ApprovalManager ------------------------------ #


def _approval_manager() -> ApprovalManager:
    return ApprovalManager(
        run_repository=InMemoryRunRepository(),
        approval_repository=InMemoryApprovalRepository(),
    )


async def _pending(
    mgr: ApprovalManager,
    *,
    user: str | None = None,
    auth_ref: str | None = None,
):
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
        auth_ref=auth_ref,
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


async def test_another_pending_call_keeps_run_pending() -> None:
    mgr = _approval_manager()
    await _pending(mgr)

    second = await mgr.create_pending(
        run_id="r1",
        thread_id="r1",
        approval_id="ap2",
        agent_id="a",
        tool_name="query_docs",
        tool_call_id="tc2",
        provider="mcp",
        description="second MCP tool",
        arguments={},
    )

    assert second.approval_id == "ap2"
    assert (await mgr.get_run("r1")).status == RunStatus.PENDING_APPROVAL


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


async def test_cancel_pending_rejects_approval_without_claiming_run() -> None:
    mgr = _approval_manager()
    await _pending(mgr, user="owner", auth_ref="session-1")

    cancelled = await mgr.cancel_pending(
        run_id="r1",
        approval_id="ap1",
        caller_user_id="owner",
        caller_auth_ref="session-1",
    )

    assert cancelled.status == ApprovalStatus.REJECTED
    assert (await mgr.get_run("r1")).status == RunStatus.PENDING_APPROVAL
    with pytest.raises(ApprovalAlreadyProcessed):
        await mgr.claim(
            run_id="r1",
            approval_id="ap1",
            caller_user_id="owner",
            caller_auth_ref="session-1",
        )


async def test_cancel_pending_loses_after_resume_claim() -> None:
    mgr = _approval_manager()
    await _pending(mgr)
    await mgr.claim(run_id="r1", approval_id="ap1")

    with pytest.raises(ApprovalAlreadyProcessed):
        await mgr.cancel_pending(run_id="r1", approval_id="ap1")


async def test_unauthorized_cancellation_leaves_approval_pending() -> None:
    mgr = _approval_manager()
    await _pending(mgr, user="owner", auth_ref="session-1")

    with pytest.raises(UnauthorizedApprover):
        await mgr.cancel_pending(
            run_id="r1",
            approval_id="ap1",
            caller_user_id="intruder",
            caller_auth_ref="session-1",
        )

    assert (await mgr.get_approval("r1", "ap1")).status == ApprovalStatus.PENDING
    assert (await mgr.get_run("r1")).status == RunStatus.PENDING_APPROVAL


async def test_claim_validates_run_membership() -> None:
    runs = InMemoryRunRepository()
    mgr = ApprovalManager(
        run_repository=runs,
        approval_repository=InMemoryApprovalRepository(),
    )
    await _pending(mgr)
    await runs.create_if_absent(RunRecord(run_id="r2", thread_id="r2", system_name="s"))
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


async def test_claim_rejects_a_different_session_before_claiming() -> None:
    mgr = _approval_manager()
    await _pending(mgr, user="owner", auth_ref="session-1")

    with pytest.raises(UnauthorizedApprover):
        await mgr.claim(
            run_id="r1",
            approval_id="ap1",
            caller_user_id="owner",
            caller_auth_ref="session-2",
        )

    claimed = await mgr.claim(
        run_id="r1",
        approval_id="ap1",
        caller_user_id="owner",
        caller_auth_ref="session-1",
    )
    assert claimed.status == ApprovalStatus.RESUMING


async def test_claim_rejects_an_omitted_session_before_claiming() -> None:
    mgr = _approval_manager()
    await _pending(mgr, user="owner", auth_ref="session-1")

    with pytest.raises(UnauthorizedApprover):
        await mgr.claim(
            run_id="r1",
            approval_id="ap1",
            caller_user_id="owner",
        )

    claimed = await mgr.claim(
        run_id="r1",
        approval_id="ap1",
        caller_user_id="owner",
        caller_auth_ref="session-1",
    )
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
