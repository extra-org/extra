"""State-machine and sanitization tests for the approval domain model."""

from __future__ import annotations

import pytest

from agent_engine.approvals.decision import RiskCategory
from agent_engine.approvals.errors import InvalidStateTransition
from agent_engine.approvals.models import (
    ApprovalRecord,
    ApprovalStatus,
    RunRecord,
    RunStatus,
    sanitize_arguments,
)


def _run() -> RunRecord:
    return RunRecord(run_id="r1", thread_id="r1", system_name="s")


def _approval() -> ApprovalRecord:
    return ApprovalRecord(
        approval_id="ap1",
        run_id="r1",
        thread_id="r1",
        agent_id="a",
        tool_name="send_email",
        tool_call_id="tc1",
        provider="local",
        category=RiskCategory.SEND,
        reason="sends externally",
        arguments={},
    )


def test_run_happy_path_transitions() -> None:
    run = _run()
    run.transition(RunStatus.PENDING_APPROVAL)
    run.transition(RunStatus.RESUMING)
    run.transition(RunStatus.COMPLETED)
    assert run.status == RunStatus.COMPLETED


@pytest.mark.parametrize(
    "start,target",
    [
        (RunStatus.COMPLETED, RunStatus.RESUMING),
        (RunStatus.FAILED, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.RESUMING),
    ],
)
def test_invalid_run_transitions_raise(start: RunStatus, target: RunStatus) -> None:
    run = _run()
    run.status = start
    with pytest.raises(InvalidStateTransition):
        run.transition(target)


def test_approval_happy_path() -> None:
    ap = _approval()
    ap.transition(ApprovalStatus.RESUMING)
    ap.transition(ApprovalStatus.APPROVED)
    assert ap.status == ApprovalStatus.APPROVED


def test_rejected_cannot_become_approved() -> None:
    ap = _approval()
    ap.transition(ApprovalStatus.REJECTED)
    with pytest.raises(InvalidStateTransition):
        ap.transition(ApprovalStatus.APPROVED)


def test_approved_is_terminal() -> None:
    ap = _approval()
    ap.transition(ApprovalStatus.RESUMING)
    ap.transition(ApprovalStatus.APPROVED)
    with pytest.raises(InvalidStateTransition):
        ap.transition(ApprovalStatus.REJECTED)


def test_sanitize_redacts_sensitive_keys() -> None:
    cleaned = sanitize_arguments(
        {"to": "a@b.com", "api_key": "sk-secret", "session_token": "abc", "amount": 10}
    )
    assert cleaned["to"] == "a@b.com"
    assert cleaned["amount"] == 10
    assert cleaned["api_key"] == "***redacted***"
    assert cleaned["session_token"] == "***redacted***"
