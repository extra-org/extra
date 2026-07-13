"""The deterministic default approval policy."""

from __future__ import annotations

from agent_engine.approvals.invocation import ToolInvocation
from agent_engine.approvals.policy import ApprovalQuery, DefaultApprovalPolicy


def _query(*, auto_mode: bool, session_allowed: bool) -> ApprovalQuery:
    inv = ToolInvocation(tool_call_id="tc1", agent_id="a", tool_name="send_email")
    return ApprovalQuery(invocation=inv, auto_mode=auto_mode, session_allowed=session_allowed)


def test_requires_approval_by_default() -> None:
    assert DefaultApprovalPolicy().requires_approval(_query(auto_mode=False, session_allowed=False))


def test_auto_mode_skips_approval() -> None:
    assert not DefaultApprovalPolicy().requires_approval(
        _query(auto_mode=True, session_allowed=False)
    )


def test_session_permission_skips_approval() -> None:
    assert not DefaultApprovalPolicy().requires_approval(
        _query(auto_mode=False, session_allowed=True)
    )
