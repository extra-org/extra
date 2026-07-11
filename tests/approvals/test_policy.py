"""Contract-based approval policy.

Runtime decisions are based on cached ToolContract values. Arbitrary runtime
argument content must not change the category except through an explicit
ConditionalEffect discriminator.
"""

from __future__ import annotations

from agent_engine.approvals.contract import ConditionalEffect, ToolContract, ToolContractSource
from agent_engine.approvals.decision import ApprovalDecision, RiskCategory, ToolCall
from agent_engine.approvals.policy import DefaultToolApprovalPolicy

policy = DefaultToolApprovalPolicy()


def _contract(
    category: RiskCategory,
    *,
    confidence: float = 0.98,
    external: bool = False,
    destructive: bool = False,
    conditional: ConditionalEffect | None = None,
) -> ToolContract:
    return ToolContract(
        category=category,
        confidence=confidence,
        source=ToolContractSource.DETERMINISTIC,
        external_side_effect=external,
        destructive=destructive,
        reason=f"{category.value} contract",
        fingerprint=f"fp_{category.value}",
        trusted=True,
        conditional_effect=conditional,
    )


def _decide(
    name: str,
    contract: ToolContract,
    *,
    arguments: dict[str, object] | None = None,
) -> ApprovalDecision:
    call = ToolCall(tool_name=name, agent_id="a", arguments=arguments or {})
    return policy.evaluate(call, contract).decision


def test_read_contract_executes_even_when_query_contains_risky_words() -> None:
    decision = _decide(
        "search_messages",
        _contract(RiskCategory.READ),
        arguments={"query": "how to delete an account"},
    )
    assert decision == ApprovalDecision.EXECUTE


def test_read_financial_or_access_nouns_do_not_escalate_without_contract() -> None:
    assert _decide("list_invoices", _contract(RiskCategory.READ)) == ApprovalDecision.EXECUTE
    assert _decide("get_user_role", _contract(RiskCategory.READ)) == ApprovalDecision.EXECUTE


def test_draft_contract_executes_only_without_external_side_effect() -> None:
    assert _decide("draft_email", _contract(RiskCategory.DRAFT)) == ApprovalDecision.EXECUTE
    assert (
        _decide("draft_email", _contract(RiskCategory.DRAFT, external=True))
        == ApprovalDecision.REQUIRE_APPROVAL
    )


def test_side_effecting_contracts_require_approval() -> None:
    for category in (
        RiskCategory.SEND,
        RiskCategory.WRITE,
        RiskCategory.DELETE,
        RiskCategory.FINANCIAL,
        RiskCategory.ACCESS_CONTROL,
        RiskCategory.CODE_EXECUTION,
        RiskCategory.UNKNOWN,
    ):
        assert (
            _decide(category.value, _contract(category, external=True))
            == ApprovalDecision.REQUIRE_APPROVAL
        )


def test_forbidden_contract_is_denied() -> None:
    assert (
        _decide("drop_database", _contract(RiskCategory.FORBIDDEN, external=True, destructive=True))
        == ApprovalDecision.DENY
    )


def test_low_confidence_read_fails_closed() -> None:
    assert (
        _decide("search_docs", _contract(RiskCategory.READ, confidence=0.70))
        == ApprovalDecision.REQUIRE_APPROVAL
    )


def test_conditional_contract_reads_only_declared_discriminator_path() -> None:
    conditional = ConditionalEffect(
        argument_path="action",
        cases={
            "read": RiskCategory.READ,
            "update": RiskCategory.WRITE,
            "delete": RiskCategory.DELETE,
        },
        default_category=RiskCategory.UNKNOWN,
    )
    contract = _contract(
        RiskCategory.CONDITIONAL,
        external=True,
        destructive=True,
        conditional=conditional,
    )

    assert (
        _decide("manage_resource", contract, arguments={"action": "read"})
        == ApprovalDecision.EXECUTE
    )
    assert (
        _decide("manage_resource", contract, arguments={"action": "update"})
        == ApprovalDecision.REQUIRE_APPROVAL
    )
    assert (
        _decide("manage_resource", contract, arguments={"action": "delete"})
        == ApprovalDecision.REQUIRE_APPROVAL
    )
    assert (
        _decide("manage_resource", contract, arguments={"action": "unexpected"})
        == ApprovalDecision.REQUIRE_APPROVAL
    )
    assert (
        _decide(
            "manage_resource",
            contract,
            arguments={"action": "read", "query": "please delete everything"},
        )
        == ApprovalDecision.EXECUTE
    )


def test_policy_is_deterministic() -> None:
    call = ToolCall(tool_name="send_report", agent_id="a", arguments={"to": "x"})
    contract = _contract(RiskCategory.SEND, external=True)
    first = policy.evaluate(call, contract)
    second = policy.evaluate(call, contract)
    assert first == second
