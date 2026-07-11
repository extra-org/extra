"""Tool contract discovery helpers: fingerprints, cache, analyzer, validator."""

from __future__ import annotations

import asyncio

from agent_engine.approvals.analyzer import DeterministicToolContractAnalyzer
from agent_engine.approvals.contract import MCPServerIdentity, MCPToolDefinition, ToolContract
from agent_engine.approvals.contract_registry import InMemoryToolContractRegistry
from agent_engine.approvals.contract_service import ToolContractService
from agent_engine.approvals.decision import RiskCategory
from agent_engine.approvals.fingerprint import tool_fingerprint
from agent_engine.approvals.validator import ToolContractValidator


def _identity(server_id: str = "docs") -> MCPServerIdentity:
    return MCPServerIdentity(server_id=server_id, url=f"https://{server_id}.example/mcp")


def _tool(
    name: str,
    *,
    description: str = "",
    schema: dict[str, object] | None = None,
    annotations: dict[str, object] | None = None,
) -> MCPToolDefinition:
    return MCPToolDefinition(
        name=name,
        description=description,
        input_schema=schema or {},
        annotations=annotations or {},
    )


def test_fingerprint_is_stable_and_key_order_independent() -> None:
    a = _tool("search", schema={"properties": {"b": {"type": "string"}, "a": {"type": "string"}}})
    b = _tool("search", schema={"properties": {"a": {"type": "string"}, "b": {"type": "string"}}})
    assert tool_fingerprint(_identity(), a) == tool_fingerprint(_identity(), b)


def test_fingerprint_changes_when_definition_or_server_changes() -> None:
    original = _tool("search", description="Search docs")
    changed = _tool("search", description="Search public docs")
    assert tool_fingerprint(_identity(), original) != tool_fingerprint(_identity(), changed)
    assert tool_fingerprint(_identity("a"), original) != tool_fingerprint(_identity("b"), original)


async def test_contract_service_uses_cache_for_same_fingerprint() -> None:
    class CountingAnalyzer(DeterministicToolContractAnalyzer):
        def __init__(self) -> None:
            self.calls = 0

        async def analyze(
            self, identity: MCPServerIdentity, tool: MCPToolDefinition
        ) -> ToolContract:
            self.calls += 1
            return await super().analyze(identity, tool)

    analyzer = CountingAnalyzer()
    service = ToolContractService(
        registry=InMemoryToolContractRegistry(),
        analyzer=analyzer,
        validator=ToolContractValidator(),
    )
    tool = _tool("search_messages")
    first = await service.get_or_create(_identity(), tool)
    second = await service.get_or_create(_identity(), tool)
    assert first == second
    assert analyzer.calls == 1


async def test_contract_registry_duplicate_saves_are_idempotent_under_threads() -> None:
    registry = InMemoryToolContractRegistry()
    service = ToolContractService(registry=registry)
    tool = _tool("search_messages")
    results = await asyncio.gather(*(service.get_or_create(_identity(), tool) for _ in range(10)))
    assert len({result.fingerprint for result in results}) == 1
    assert all(result == results[0] for result in results)


async def test_deterministic_analyzer_handles_required_examples() -> None:
    analyzer = DeterministicToolContractAnalyzer()
    assert (
        await analyzer.analyze(_identity(), _tool("search_emails"))
    ).category == RiskCategory.READ
    assert (
        await analyzer.analyze(_identity(), _tool("list_invoices"))
    ).category == RiskCategory.READ
    assert (
        await analyzer.analyze(_identity(), _tool("get_user_role"))
    ).category == RiskCategory.READ
    assert (
        await analyzer.analyze(_identity(), _tool("draft_email"))
    ).category == RiskCategory.DRAFT
    assert (await analyzer.analyze(_identity(), _tool("send_email"))).category == RiskCategory.SEND
    assert (
        await analyzer.analyze(_identity(), _tool("create_issue"))
    ).category == RiskCategory.WRITE
    assert (
        await analyzer.analyze(_identity(), _tool("delete_email"))
    ).category == RiskCategory.DELETE
    assert (
        await analyzer.analyze(_identity(), _tool("charge_customer"))
    ).category == RiskCategory.FINANCIAL


async def test_analyzer_creates_conditional_contract_from_enum_discriminator() -> None:
    contract = await DeterministicToolContractAnalyzer().analyze(
        _identity(),
        _tool(
            "manage_resource",
            schema={
                "properties": {"action": {"type": "string", "enum": ["read", "update", "delete"]}}
            },
        ),
    )
    assert contract.category == RiskCategory.CONDITIONAL
    assert contract.conditional_effect is not None
    assert contract.conditional_effect.resolve({"action": "read"}) == RiskCategory.READ
    assert contract.conditional_effect.resolve({"action": "update"}) == RiskCategory.WRITE
    assert contract.conditional_effect.resolve({"action": "delete"}) == RiskCategory.DELETE
    assert contract.conditional_effect.resolve({"action": "unexpected"}) == RiskCategory.UNKNOWN


async def test_validator_fails_closed_for_invalid_conditional_path() -> None:
    contract = await DeterministicToolContractAnalyzer().analyze(
        _identity(),
        _tool(
            "manage_resource",
            schema={"properties": {"action": {"type": "string", "enum": ["read"]}}},
        ),
    )
    assert contract.conditional_effect is not None
    broken = ToolContract(
        category=RiskCategory.CONDITIONAL,
        confidence=contract.confidence,
        source=contract.source,
        external_side_effect=contract.external_side_effect,
        destructive=contract.destructive,
        reason=contract.reason,
        fingerprint=contract.fingerprint,
        conditional_effect=contract.conditional_effect,
    )
    object.__setattr__(broken.conditional_effect, "argument_path", "missing")
    validated = ToolContractValidator().validate(
        _tool("manage_resource", schema={"properties": {"action": {"type": "string"}}}),
        broken,
    )
    assert validated.category == RiskCategory.UNKNOWN
