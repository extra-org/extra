"""API-layer contract logic: approval-error → HTTP mapping and the sanitized
pending-approval response model."""

from __future__ import annotations

from agent_engine.api.app import _map_approval_error, _pending_model
from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalNotFound,
    ApprovalRunMismatch,
    InvalidDecision,
    RunNotFound,
    UnauthorizedApprover,
)
from agent_engine.engine.types import PendingApproval


def test_error_status_mapping() -> None:
    assert _map_approval_error(RunNotFound("r")).status_code == 404
    assert _map_approval_error(ApprovalNotFound("a")).status_code == 404
    assert _map_approval_error(ApprovalRunMismatch("a", "r")).status_code == 404
    assert _map_approval_error(UnauthorizedApprover("a")).status_code == 403
    assert _map_approval_error(ApprovalAlreadyProcessed("a", "approved")).status_code == 409
    assert _map_approval_error(InvalidDecision("maybe")).status_code == 400


def test_pending_model_none_passthrough() -> None:
    assert _pending_model(None) is None


def test_pending_model_carries_sanitized_fields() -> None:
    pa = PendingApproval(
        run_id="r1",
        approval_id="ap1",
        agent_id="writer",
        tool_name="send_email",
        reason="sends externally",
        category="send",
        provider="mcp",
        server_id="srv",
        arguments={"to": "x@y.com", "api_key": "***redacted***"},
    )
    model = _pending_model(pa)
    assert model is not None
    assert model.run_id == "r1"
    assert model.approval_id == "ap1"
    assert model.tool_name == "send_email"
    assert model.provider == "mcp"
    assert model.arguments["api_key"] == "***redacted***"
