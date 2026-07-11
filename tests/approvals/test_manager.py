"""ToolExecutionManager (decide/auto_mode/idempotency) and ApprovalManager
(lifecycle, validation, atomic claim) behavior."""

from __future__ import annotations

from typing import Any

import pytest

from agent_engine.approvals.contract import ToolContract, ToolContractSource
from agent_engine.approvals.decision import ApprovalDecision, ToolCall
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
from agent_engine.approvals.policy import DefaultToolApprovalPolicy
from agent_engine.approvals.repository import (
    InMemoryApprovalRepository,
    InMemoryRunRepository,
    InMemoryToolExecutionRepository,
)


def _manager() -> ToolExecutionManager:
    return ToolExecutionManager(
        policy=DefaultToolApprovalPolicy(),
        execution_repository=InMemoryToolExecutionRepository(),
    )


def _call(name: str, **kw: Any) -> ToolCall:
    return ToolCall(tool_name=name, agent_id="a", **kw)


def _contract(category, *, external: bool = False, destructive: bool = False) -> ToolContract:
    return ToolContract(
        category=category,
        confidence=0.98,
        source=ToolContractSource.DETERMINISTIC,
        external_side_effect=external,
        destructive=destructive,
        reason=f"{category.value} contract",
        fingerprint=f"fp_{category.value}",
        trusted=True,
    )


def test_decide_safe_tool_executes() -> None:
    from agent_engine.approvals.decision import RiskCategory

    v = _manager().decide(
        _call("search_docs"), auto_mode=False, contract=_contract(RiskCategory.READ)
    )
    assert v.decision == ApprovalDecision.EXECUTE
    assert v.auto_mode_applied is False


def test_decide_risky_tool_requires_approval_without_auto_mode() -> None:
    from agent_engine.approvals.decision import RiskCategory

    v = _manager().decide(
        _call("send_email"), auto_mode=False, contract=_contract(RiskCategory.SEND, external=True)
    )
    assert v.decision == ApprovalDecision.REQUIRE_APPROVAL


def test_auto_mode_bypasses_require_approval() -> None:
    from agent_engine.approvals.decision import RiskCategory

    v = _manager().decide(
        _call("send_email"), auto_mode=True, contract=_contract(RiskCategory.SEND, external=True)
    )
    assert v.decision == ApprovalDecision.EXECUTE
    assert v.auto_mode_applied is True


def test_auto_mode_never_bypasses_deny() -> None:
    from agent_engine.approvals.decision import RiskCategory

    v = _manager().decide(
        _call("drop_database"),
        auto_mode=True,
        contract=_contract(RiskCategory.FORBIDDEN, external=True, destructive=True),
    )
    assert v.decision == ApprovalDecision.DENY
    assert v.auto_mode_applied is False


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


# ------------------------------- ApprovalManager ------------------------------ #


def _approval_manager() -> ApprovalManager:
    return ApprovalManager(
        run_repository=InMemoryRunRepository(),
        approval_repository=InMemoryApprovalRepository(),
    )


async def _pending(mgr: ApprovalManager, *, user: str | None = None):
    from agent_engine.approvals.decision import RiskCategory

    assessment = DefaultToolApprovalPolicy().evaluate(
        _call("send_email"), _contract(RiskCategory.SEND, external=True)
    )
    return await mgr.create_pending(
        run_id="r1",
        thread_id="r1",
        approval_id="ap1",
        agent_id="a",
        tool_name="send_email",
        tool_call_id="tc1",
        provider="local",
        assessment=assessment,
        arguments={"to": "x@y.com", "api_key": "secret"},
        authorized_user_id=user,
    )


async def test_create_pending_sets_run_pending_and_sanitizes() -> None:
    mgr = _approval_manager()
    record = await _pending(mgr)
    assert record.status == ApprovalStatus.PENDING
    assert record.arguments["api_key"] == "***redacted***"
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
