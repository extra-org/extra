"""Deterministic validation for inferred tool contracts."""

from __future__ import annotations

from collections.abc import Mapping

from agent_engine.approvals.contract import (
    AUTO_EXECUTE_CONFIDENCE_THRESHOLD,
    ConditionalEffect,
    MCPToolDefinition,
    ToolContract,
    ToolContractSource,
    unknown_contract,
)
from agent_engine.approvals.decision import RiskCategory

_SIDE_EFFECT_CATEGORIES = frozenset(
    {
        RiskCategory.SEND,
        RiskCategory.WRITE,
        RiskCategory.DELETE,
        RiskCategory.FINANCIAL,
        RiskCategory.ACCESS_CONTROL,
        RiskCategory.CODE_EXECUTION,
    }
)


class ToolContractValidator:
    """Validate inferred contracts and fail closed on unreliable results.

    The validator is host-controlled deterministic policy. It treats LLM output
    and MCP annotations as evidence, not authority.
    """

    def validate(self, tool: MCPToolDefinition, contract: ToolContract) -> ToolContract:
        if not 0.0 <= contract.confidence <= 1.0:
            return unknown_contract(
                contract.fingerprint,
                reason="tool contract confidence is outside 0.0..1.0",
                trusted=contract.trusted,
            )
        if contract.source == ToolContractSource.LLM_INFERENCE and (
            contract.category == RiskCategory.FORBIDDEN
        ):
            return unknown_contract(
                contract.fingerprint,
                reason="llm inference cannot mark tools forbidden",
                trusted=contract.trusted,
            )
        if contract.category == RiskCategory.CONDITIONAL:
            if contract.conditional_effect is None:
                return unknown_contract(
                    contract.fingerprint,
                    reason="conditional contract is missing conditional effect",
                    trusted=contract.trusted,
                )
            invalid = self._validate_conditional(tool, contract.conditional_effect)
            if invalid is not None:
                return unknown_contract(
                    contract.fingerprint, reason=invalid, trusted=contract.trusted
                )
        if contract.category in (RiskCategory.READ, RiskCategory.DRAFT):
            if contract.confidence < AUTO_EXECUTE_CONFIDENCE_THRESHOLD:
                return unknown_contract(
                    contract.fingerprint,
                    reason="read/draft contract confidence is below auto-execute threshold",
                    trusted=contract.trusted,
                )
            if contract.category == RiskCategory.DRAFT and contract.external_side_effect:
                return unknown_contract(
                    contract.fingerprint,
                    reason="draft contract declares an external side effect",
                    trusted=contract.trusted,
                )
        if contract.category in _SIDE_EFFECT_CATEGORIES and not contract.external_side_effect:
            return ToolContract(
                category=contract.category,
                confidence=min(contract.confidence, 0.85),
                source=contract.source,
                external_side_effect=True,
                destructive=contract.destructive,
                reason=f"{contract.reason}; corrected side-effect flag",
                fingerprint=contract.fingerprint,
                trusted=contract.trusted,
                conditional_effect=contract.conditional_effect,
            )
        return contract

    def _validate_conditional(
        self, tool: MCPToolDefinition, conditional: ConditionalEffect
    ) -> str | None:
        if not conditional.argument_path:
            return "conditional contract is missing discriminator path"
        if not _schema_has_path(tool.input_schema, conditional.argument_path):
            return "conditional discriminator path is absent from input schema"
        if RiskCategory.CONDITIONAL in conditional.cases.values():
            return "conditional cases cannot nest conditional categories"
        if RiskCategory.FORBIDDEN in conditional.cases.values():
            return "conditional cases cannot declare forbidden operations"
        if conditional.default_category in (RiskCategory.CONDITIONAL, RiskCategory.FORBIDDEN):
            return "conditional default category is unsupported"
        return None


def _schema_has_path(schema: Mapping[str, object], path: str) -> bool:
    current: object = schema
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return False
        properties = current.get("properties")
        if not isinstance(properties, Mapping) or part not in properties:
            return False
        current = properties[part]
    return True
