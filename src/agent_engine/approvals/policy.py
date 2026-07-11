"""Deterministic, conservative tool-approval policy.

Runtime approval uses discovery-time :class:`ToolContract` values. It never calls
an LLM and never scans arbitrary runtime argument values for risk keywords; for a
conditional contract it reads only the declared discriminator path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from agent_engine.approvals.contract import AUTO_EXECUTE_CONFIDENCE_THRESHOLD, ToolContract
from agent_engine.approvals.decision import (
    ApprovalDecision,
    RiskAssessment,
    RiskCategory,
    ToolCall,
)


class ToolApprovalPolicy(ABC):
    """Strategy interface: assess one tool call's risk.

    Implementations must be pure and deterministic — the same call yields the
    same assessment — so decisions are reproducible across pods and across a
    checkpoint/resume boundary. ``auto_mode`` is intentionally *not* an input:
    the execution manager applies it after the policy runs.
    """

    @abstractmethod
    def evaluate(self, call: ToolCall, contract: ToolContract) -> RiskAssessment: ...


class DefaultToolApprovalPolicy(ToolApprovalPolicy):
    """The engine's built-in conservative policy.

    Maps each :class:`RiskCategory` to an :class:`ApprovalDecision`. Read/draft
    execute; anything with an external or persistent side effect requires
    approval; the forbidden set is denied; unknown/ambiguous is treated as
    requiring approval (fail-safe).
    """

    _DECISIONS: ClassVar[dict[RiskCategory, ApprovalDecision]] = {
        RiskCategory.READ: ApprovalDecision.EXECUTE,
        RiskCategory.DRAFT: ApprovalDecision.EXECUTE,
        RiskCategory.SEND: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.WRITE: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.DELETE: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.FINANCIAL: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.ACCESS_CONTROL: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.CODE_EXECUTION: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.FORBIDDEN: ApprovalDecision.DENY,
        RiskCategory.UNKNOWN: ApprovalDecision.REQUIRE_APPROVAL,
        RiskCategory.CONDITIONAL: ApprovalDecision.REQUIRE_APPROVAL,
    }

    def evaluate(self, call: ToolCall, contract: ToolContract) -> RiskAssessment:
        category = contract.effective_category(call.arguments)
        decision = self._decision_for(category, contract)
        reason = contract.reason
        if contract.category == RiskCategory.CONDITIONAL:
            reason = f"{contract.reason}; resolved conditional category={category.value}"
        return RiskAssessment(decision=decision, category=category, reason=reason)

    def _decision_for(self, category: RiskCategory, contract: ToolContract) -> ApprovalDecision:
        if category in (RiskCategory.READ, RiskCategory.DRAFT):
            if contract.confidence < AUTO_EXECUTE_CONFIDENCE_THRESHOLD:
                return ApprovalDecision.REQUIRE_APPROVAL
            if category == RiskCategory.DRAFT and contract.external_side_effect:
                return ApprovalDecision.REQUIRE_APPROVAL
            return ApprovalDecision.EXECUTE
        return self._DECISIONS[category]
