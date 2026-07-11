"""Tool-contract lookup, analysis, validation, and persistence."""

from __future__ import annotations

import logging

from agent_engine.approvals.analyzer import (
    DeterministicToolContractAnalyzer,
    ToolContractAnalyzer,
)
from agent_engine.approvals.contract import MCPServerIdentity, MCPToolDefinition, ToolContract
from agent_engine.approvals.contract_registry import (
    InMemoryToolContractRegistry,
    ToolContractRegistry,
)
from agent_engine.approvals.fingerprint import tool_fingerprint
from agent_engine.approvals.validator import ToolContractValidator
from agent_engine.logging_config import log

logger = logging.getLogger(__name__)


class ToolContractService:
    """Orchestrates the discovery-time contract lifecycle."""

    def __init__(
        self,
        *,
        registry: ToolContractRegistry | None = None,
        analyzer: ToolContractAnalyzer | None = None,
        validator: ToolContractValidator | None = None,
    ) -> None:
        self._registry = registry or InMemoryToolContractRegistry()
        self._analyzer = analyzer or DeterministicToolContractAnalyzer()
        self._validator = validator or ToolContractValidator()

    async def get_or_create(
        self, identity: MCPServerIdentity, tool: MCPToolDefinition
    ) -> ToolContract:
        fingerprint = tool_fingerprint(identity, tool)
        existing = self._registry.get(fingerprint)
        if existing is not None:
            log(
                logger,
                logging.INFO,
                "tool_contract_cache_hit",
                server=identity.server_id,
                tool=tool.name,
                fingerprint=fingerprint,
                category=existing.category.value,
                confidence=existing.confidence,
                source=existing.source.value,
            )
            return existing
        log(
            logger,
            logging.INFO,
            "tool_contract_cache_miss",
            server=identity.server_id,
            tool=tool.name,
            fingerprint=fingerprint,
        )
        log(
            logger,
            logging.INFO,
            "tool_contract_classification_started",
            server=identity.server_id,
            tool=tool.name,
            fingerprint=fingerprint,
        )
        inferred = await self._analyzer.analyze(identity, tool)
        validated = self._validator.validate(tool, inferred)
        saved = self._registry.save(validated)
        log(
            logger,
            logging.INFO,
            "tool_contract_saved",
            server=identity.server_id,
            tool=tool.name,
            fingerprint=saved.fingerprint,
            category=saved.category.value,
            confidence=saved.confidence,
            source=saved.source.value,
        )
        return saved
