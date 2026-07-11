"""Discovery-time tool contract analysis."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Protocol

from agent_engine.approvals.contract import (
    ConditionalEffect,
    MCPServerIdentity,
    MCPToolDefinition,
    ToolContract,
    ToolContractSource,
    unknown_contract,
)
from agent_engine.approvals.decision import RiskCategory
from agent_engine.approvals.fingerprint import tool_fingerprint
from agent_engine.logging_config import log

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
_FORBIDDEN_EXACT = frozenset(
    {
        "delete_all",
        "drop_database",
        "drop_all",
        "factory_reset",
        "wipe",
        "wipe_disk",
        "format_disk",
        "rm_rf",
        "disable_security",
    }
)
_READ_VERBS = frozenset(
    {
        "read",
        "search",
        "list",
        "fetch",
        "query",
        "get",
        "view",
        "find",
        "lookup",
        "describe",
        "inspect",
        "count",
        "check",
        "status",
        "summarize",
    }
)
_DRAFT_VERBS = frozenset({"draft", "preview", "compose", "prepare", "simulate"})
_SEND_VERBS = frozenset({"send", "publish", "post", "notify", "dispatch", "broadcast", "share"})
_DELETE_VERBS = frozenset(
    {"delete", "remove", "revoke", "disable", "cancel", "drop", "purge", "destroy"}
)
_WRITE_VERBS = frozenset(
    {"create", "update", "modify", "write", "edit", "set", "add", "insert", "save", "patch"}
)
_FINANCIAL_VERBS = frozenset(
    {"pay", "charge", "refund", "transfer", "wire", "purchase", "checkout", "withdraw"}
)
_ACCESS_VERBS = frozenset({"grant", "authorize", "assign", "allow"})
_ACCESS_OBJECTS = frozenset(
    {"role", "roles", "permission", "permissions", "acl", "scope", "scopes"}
)
_CODE_VERBS = frozenset({"execute", "exec", "eval", "shell", "command", "deploy", "run_code"})


class StructuredToolContractClassifier(Protocol):
    """Optional structured inference adapter used only during discovery."""

    async def classify(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ToolContractAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, identity: MCPServerIdentity, tool: MCPToolDefinition) -> ToolContract:
        """Return an inferred contract for a discovered tool definition."""


class DeterministicToolContractAnalyzer(ToolContractAnalyzer):
    """Host-controlled definition-only analyzer.

    It intentionally avoids broad substring matching and never inspects runtime
    arguments. Name tokens are the strongest signal; annotations and schema are
    only hints. Ambiguous definitions fail closed to UNKNOWN.
    """

    async def analyze(self, identity: MCPServerIdentity, tool: MCPToolDefinition) -> ToolContract:
        fingerprint = tool_fingerprint(identity, tool)
        norm_name = _normalize_name(tool.name)
        tokens = _tokens(tool.name)
        token_set = set(tokens)
        first = next(iter(tokens), "")

        if norm_name in _FORBIDDEN_EXACT:
            return ToolContract(
                category=RiskCategory.FORBIDDEN,
                confidence=1.0,
                source=ToolContractSource.DETERMINISTIC,
                external_side_effect=True,
                destructive=True,
                reason="matches a host-controlled forbidden tool identifier",
                fingerprint=fingerprint,
                trusted=identity.trusted,
            )
        conditional = _conditional_from_schema(tool.input_schema)
        if conditional is not None:
            return ToolContract(
                category=RiskCategory.CONDITIONAL,
                confidence=0.98,
                source=ToolContractSource.DETERMINISTIC,
                external_side_effect=True,
                destructive=True,
                reason="tool declares an explicit operation discriminator",
                fingerprint=fingerprint,
                trusted=identity.trusted,
                conditional_effect=conditional,
            )
        if first in _READ_VERBS:
            return _contract(
                RiskCategory.READ,
                "tool name declares a read-only operation",
                identity,
                fingerprint,
                external=False,
                destructive=False,
            )
        if first in _DRAFT_VERBS:
            return _contract(
                RiskCategory.DRAFT,
                "tool name declares draft or preview behavior",
                identity,
                fingerprint,
                external=False,
                destructive=False,
            )
        if first in _SEND_VERBS:
            return _contract(
                RiskCategory.SEND,
                "tool name declares external send/publish behavior",
                identity,
                fingerprint,
            )
        if first in _FINANCIAL_VERBS:
            return _contract(
                RiskCategory.FINANCIAL,
                "tool name declares a financial operation",
                identity,
                fingerprint,
            )
        if first in _DELETE_VERBS:
            return _contract(
                RiskCategory.DELETE,
                "tool name declares destructive behavior",
                identity,
                fingerprint,
                destructive=True,
            )
        if first in _CODE_VERBS:
            return _contract(
                RiskCategory.CODE_EXECUTION,
                "tool name declares code or command execution",
                identity,
                fingerprint,
            )
        if first in _WRITE_VERBS:
            if token_set & _ACCESS_OBJECTS:
                return _contract(
                    RiskCategory.ACCESS_CONTROL,
                    "tool name declares access-control modification",
                    identity,
                    fingerprint,
                )
            return _contract(
                RiskCategory.WRITE,
                "tool name declares persistent modification",
                identity,
                fingerprint,
            )
        if first in _ACCESS_VERBS and token_set & _ACCESS_OBJECTS:
            return _contract(
                RiskCategory.ACCESS_CONTROL,
                "tool name declares access-control modification",
                identity,
                fingerprint,
            )

        annotations = tool.annotations or {}
        if identity.trusted and annotations.get("readOnlyHint") is True:
            return ToolContract(
                category=RiskCategory.READ,
                confidence=0.96,
                source=ToolContractSource.MCP_ANNOTATIONS,
                external_side_effect=False,
                destructive=False,
                reason="trusted MCP annotations declare read-only behavior",
                fingerprint=fingerprint,
                trusted=identity.trusted,
            )
        return unknown_contract(
            fingerprint,
            reason="tool behavior could not be determined from discovery metadata",
            trusted=identity.trusted,
        )


class StructuredLLMToolContractAnalyzer(ToolContractAnalyzer):
    """Discovery-time structured LLM inference wrapper.

    The injected classifier must return schema-like data. Runtime arguments are
    never included in the payload. Any failure returns UNKNOWN.
    """

    def __init__(self, classifier: StructuredToolContractClassifier) -> None:
        self._classifier = classifier

    async def analyze(self, identity: MCPServerIdentity, tool: MCPToolDefinition) -> ToolContract:
        fingerprint = tool_fingerprint(identity, tool)
        payload = {
            "server": {"id": identity.server_id, "url": identity.url, "trusted": identity.trusted},
            "tool": {
                "name": tool.name,
                "description": tool.description,
                "input_schema": dict(tool.input_schema),
                "annotations": dict(tool.annotations),
            },
        }
        try:
            raw = await self._classifier.classify(payload)
            category = RiskCategory(str(raw["category"]).lower())
            conditional = _conditional_from_raw(raw.get("conditionalEffect"))
            return ToolContract(
                category=category,
                confidence=float(raw["confidence"]),
                source=ToolContractSource.LLM_INFERENCE,
                external_side_effect=bool(raw["externalSideEffect"]),
                destructive=bool(raw["destructive"]),
                reason=str(raw["reason"]),
                fingerprint=fingerprint,
                trusted=identity.trusted,
                conditional_effect=conditional,
            )
        except Exception as exc:
            log(
                logger,
                logging.WARNING,
                "tool_contract_classification_failed",
                server=identity.server_id,
                tool=tool.name,
                fingerprint=fingerprint,
                reason=type(exc).__name__,
            )
            return unknown_contract(
                fingerprint,
                reason="structured tool classification failed",
                trusted=identity.trusted,
            )


def _contract(
    category: RiskCategory,
    reason: str,
    identity: MCPServerIdentity,
    fingerprint: str,
    *,
    external: bool = True,
    destructive: bool = False,
) -> ToolContract:
    return ToolContract(
        category=category,
        confidence=0.98,
        source=ToolContractSource.DETERMINISTIC,
        external_side_effect=external,
        destructive=destructive,
        reason=reason,
        fingerprint=fingerprint,
        trusted=identity.trusted,
    )


def _normalize_name(name: str) -> str:
    return "_".join(_tokens(name))


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.lower().replace("-", "_")))


def _conditional_from_schema(schema: Mapping[str, Any]) -> ConditionalEffect | None:
    props = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(props, Mapping):
        return None
    for candidate in ("action", "operation"):
        raw = props.get(candidate)
        if not isinstance(raw, Mapping):
            continue
        values = raw.get("enum")
        if not isinstance(values, list):
            continue
        cases: dict[str, RiskCategory] = {}
        for value in values:
            if not isinstance(value, str):
                continue
            cases[value] = _case_category(value)
        if cases:
            return ConditionalEffect(
                argument_path=candidate,
                cases=cases,
                default_category=RiskCategory.UNKNOWN,
            )
    return None


def _conditional_from_raw(value: object) -> ConditionalEffect | None:
    if not isinstance(value, Mapping):
        return None
    cases_raw = value.get("cases")
    if not isinstance(cases_raw, Mapping):
        return None
    return ConditionalEffect(
        argument_path=str(value.get("argumentPath", "")),
        cases={str(k): RiskCategory(str(v).lower()) for k, v in cases_raw.items()},
        default_category=RiskCategory(str(value.get("defaultCategory", "unknown")).lower()),
    )


def _case_category(value: str) -> RiskCategory:
    token = _tokens(value)
    first = token[0] if token else ""
    if first in _READ_VERBS:
        return RiskCategory.READ
    if first in _DRAFT_VERBS:
        return RiskCategory.DRAFT
    if first in _SEND_VERBS:
        return RiskCategory.SEND
    if first in _FINANCIAL_VERBS:
        return RiskCategory.FINANCIAL
    if first in _DELETE_VERBS:
        return RiskCategory.DELETE
    if first in _CODE_VERBS:
        return RiskCategory.CODE_EXECUTION
    if first in _WRITE_VERBS:
        return RiskCategory.WRITE
    return RiskCategory.UNKNOWN
