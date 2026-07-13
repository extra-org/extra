"""The single free-text -> typed decision parsing boundary."""

from __future__ import annotations

import pytest

from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.errors import InvalidDecision


@pytest.mark.parametrize(
    "value,expected",
    [
        ("approve", ApprovalDecision.ALLOW_ONCE),
        ("allow", ApprovalDecision.ALLOW_ONCE),
        ("allow once", ApprovalDecision.ALLOW_ONCE),
        ("allowed once", ApprovalDecision.ALLOW_ONCE),
        ("Allow For This Session", ApprovalDecision.ALLOW_FOR_SESSION),
        ("allowed for this session", ApprovalDecision.ALLOW_FOR_SESSION),
        ("deny", ApprovalDecision.DENY),
        ("reject", ApprovalDecision.DENY),
        ("  DENY  ", ApprovalDecision.DENY),
    ],
)
def test_recognized_aliases(value: str, expected: ApprovalDecision) -> None:
    assert parse_decision(value) == expected


def test_enum_passthrough() -> None:
    assert parse_decision(ApprovalDecision.DENY) is ApprovalDecision.DENY


def test_mapping_payload() -> None:
    assert parse_decision({"decision": "allow for this session"}) == (
        ApprovalDecision.ALLOW_FOR_SESSION
    )


def test_unknown_raises_without_default() -> None:
    with pytest.raises(InvalidDecision):
        parse_decision("maybe later")


def test_unknown_uses_default_when_given() -> None:
    # Fail-closed inside the runtime: an unrecognized resume becomes DENY.
    assert parse_decision("garbage", default=ApprovalDecision.DENY) == ApprovalDecision.DENY
    assert parse_decision(None, default=ApprovalDecision.DENY) == ApprovalDecision.DENY
