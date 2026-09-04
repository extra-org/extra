"""Process-local idempotency ledger for tool executions."""

from __future__ import annotations

import asyncio
from typing import Any

from agent_engine.approvals.models import ToolExecutionRecord
from agent_engine.approvals.tool_execution_repository import ToolExecutionRepository


class InMemoryToolExecutionRepository(ToolExecutionRepository):
    def __init__(self) -> None:
        self._records: dict[str, ToolExecutionRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, execution_id: str) -> ToolExecutionRecord | None:
        async with self._lock:
            return self._records.get(execution_id)

    async def start(self, record: ToolExecutionRecord) -> tuple[ToolExecutionRecord, bool]:
        async with self._lock:
            existing = self._records.get(record.execution_id)
            if existing is not None:
                return existing, False
            self._records[record.execution_id] = record
            return record, True

    async def complete(
        self,
        execution_id: str,
        status: str,
        result: str,
        structured: Any | None = None,
        artifact: Any | None = None,
    ) -> None:
        async with self._lock:
            record = self._records.get(execution_id)
            if record is not None:
                record.status = status
                record.result = result
                record.structured = structured
                record.artifact = artifact
