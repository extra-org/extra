"""Persistence contract for tool-execution idempotency."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent_engine.approvals.models import ToolExecutionRecord, ToolExecutionStatus
from agent_engine.runtime.tool_results import PersistedToolResult


class ToolExecutionRepository(ABC):
    @abstractmethod
    async def get(self, execution_id: str) -> ToolExecutionRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def start(self, record: ToolExecutionRecord) -> tuple[ToolExecutionRecord, bool]:
        raise NotImplementedError

    @abstractmethod
    async def wait_for_completion(self, execution_id: str) -> ToolExecutionRecord:
        """Wait for the owner of an existing execution to publish its result."""
        raise NotImplementedError

    @abstractmethod
    async def complete(
        self,
        execution_id: str,
        status: ToolExecutionStatus,
        result: PersistedToolResult,
    ) -> None:
        raise NotImplementedError
