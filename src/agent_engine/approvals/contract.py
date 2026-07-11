"""Tool-contract domain model.

A :class:`ToolContract` is the engine's *inferred, cached* description of what a
tool does — produced once at discovery, keyed by a stable fingerprint, and then
consulted deterministically at runtime. It replaces per-call keyword scanning of
arbitrary runtime arguments.

Pure domain types: no LangGraph, MCP-client, or LLM imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from agent_engine.approvals.decision import RiskCategory

# Confidence at/above which a READ/DRAFT contract may auto-execute (no approval).
# Defined once — never inline this number elsewhere.
AUTO_EXECUTE_CONFIDENCE_THRESHOLD = 0.95


class ToolContractSource(StrEnum):
    """How a contract's classification was produced."""

    MCP_ANNOTATIONS = "mcp_annotations"
    LLM_INFERENCE = "llm_inference"
    COMBINED = "combined"
    DETERMINISTIC = "deterministic"  # host-controlled rule (e.g. forbidden list)
    FALLBACK = "fallback"  # insufficient/failed evidence → fail-closed UNKNOWN


@dataclass(frozen=True)
class MCPServerIdentity:
    """Stable identity + trust of a tool's origin.

    Two different MCP servers exposing a tool with the same name must not share
    an identity, so the identity — not just the tool name — scopes contracts.
    ``trusted`` gates whether a READ/DRAFT may auto-execute: first-party/local
    tools are trusted; MCP servers are untrusted unless explicitly configured.
    """

    server_id: str
    url: str = ""
    trusted: bool = False

    @property
    def key(self) -> str:
        return f"{self.server_id}|{self.url}"


# The synthetic identity for in-process (first-party) local plugin tools. Local
#: tools are code the host wrote, so they are trusted.
LOCAL_SERVER_IDENTITY = MCPServerIdentity(server_id="__local__", url="", trusted=True)


@dataclass(frozen=True)
class MCPToolDefinition:
    """The discovery-time definition of a tool (never runtime arguments)."""

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    annotations: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConditionalEffect:
    """A tool whose effect depends on one explicit discriminator argument.

    Only ``argument_path`` is ever inspected at runtime; no other argument value
    influences the category. ``cases`` maps discriminator value → category;
    anything not listed resolves to ``default_category``.
    """

    argument_path: str
    cases: Mapping[str, RiskCategory]
    default_category: RiskCategory = RiskCategory.UNKNOWN

    def __post_init__(self) -> None:
        # Freeze the mapping so the "immutable" contract cannot be mutated in place.
        object.__setattr__(self, "cases", MappingProxyType(dict(self.cases)))

    def resolve(self, arguments: Mapping[str, Any]) -> RiskCategory:
        """Resolve the effective category from the declared discriminator only."""
        value = _read_path(arguments, self.argument_path)
        if value is None:
            return self.default_category
        return self.cases.get(str(value), self.default_category)


@dataclass(frozen=True)
class ToolContract:
    """Immutable, cached classification of a tool's expected side effects."""

    category: RiskCategory
    confidence: float
    source: ToolContractSource
    external_side_effect: bool
    destructive: bool
    reason: str
    fingerprint: str
    trusted: bool = False
    conditional_effect: ConditionalEffect | None = None

    def effective_category(self, arguments: Mapping[str, Any]) -> RiskCategory:
        """The category to enforce for a concrete call.

        For CONDITIONAL contracts this resolves the declared discriminator; for
        everything else it is the static category.
        """
        if self.category == RiskCategory.CONDITIONAL and self.conditional_effect is not None:
            return self.conditional_effect.resolve(arguments)
        return self.category


def _read_path(data: Mapping[str, Any], path: str) -> Any:
    """Read a dotted ``a.b.c`` path from nested mappings; None if absent."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def unknown_contract(fingerprint: str, *, reason: str, trusted: bool = False) -> ToolContract:
    """Fail-closed contract: UNKNOWN → REQUIRE_APPROVAL at runtime.

    Used whenever classification cannot be trusted (missing/corrupt cache, LLM
    failure, validation failure, malformed definition). A classification failure
    must never yield an auto-executable contract.
    """
    return ToolContract(
        category=RiskCategory.UNKNOWN,
        confidence=0.0,
        source=ToolContractSource.FALLBACK,
        external_side_effect=True,
        destructive=False,
        reason=reason,
        fingerprint=fingerprint,
        trusted=trusted,
    )
