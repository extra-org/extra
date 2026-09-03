"""Persistence contract for tool-execution idempotency."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent_engine.approvals.models import ToolExecutionRecord


class ToolExecutionRepository(ABC):
    @abstractmethod
    async def get(self, execution_id: str) -> ToolExecutionRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def start(self, record: ToolExecutionRecord) -> tuple[ToolExecutionRecord, bool]:
        raise NotImplementedError

    @abstractmethod
    async def complete(
        self,
        execution_id: str,
        status: str,
        result: str,
        structured: Any | None = None,
    ) -> None:
        raise NotImplementedError
