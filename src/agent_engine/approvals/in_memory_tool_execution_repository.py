"""Process-local idempotency ledger for tool executions."""

from __future__ import annotations

import asyncio
import copy
import dataclasses

from agent_engine.approvals.models import ToolExecutionRecord, ToolExecutionStatus
from agent_engine.approvals.tool_execution_repository import ToolExecutionRepository
from agent_engine.runtime.tool_results import PersistedToolResult


class InMemoryToolExecutionRepository(ToolExecutionRepository):
    def __init__(self) -> None:
        self._records: dict[str, ToolExecutionRecord] = {}
        self._completed: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def get(self, execution_id: str) -> ToolExecutionRecord | None:
        async with self._lock:
            record = self._records.get(execution_id)
            return _copy_record(record) if record is not None else None

    async def start(self, record: ToolExecutionRecord) -> tuple[ToolExecutionRecord, bool]:
        async with self._lock:
            existing = self._records.get(record.execution_id)
            if existing is not None:
                return _copy_record(existing), False
            stored = _copy_record(record)
            self._records[record.execution_id] = stored
            self._completed[record.execution_id] = asyncio.Event()
            return _copy_record(stored), True

    async def wait_for_completion(self, execution_id: str) -> ToolExecutionRecord:
        async with self._lock:
            record = self._records.get(execution_id)
            if record is None:
                raise KeyError(f"tool execution not found: {execution_id}")
            if record.status != "started":
                return _copy_record(record)
            completed = self._completed[execution_id]
        await completed.wait()
        async with self._lock:
            return _copy_record(self._records[execution_id])

    async def complete(
        self,
        execution_id: str,
        status: ToolExecutionStatus,
        result: PersistedToolResult,
    ) -> None:
        if status == "started":
            raise ValueError("completed tool execution must be terminal")
        async with self._lock:
            record = self._records.get(execution_id)
            if record is None:
                raise KeyError(f"tool execution not found: {execution_id}")
            if record.status != "started":
                raise ValueError(f"tool execution is already terminal: {execution_id}")
            record.status = status
            record.result = copy.deepcopy(result)
            self._completed[execution_id].set()


def _copy_record(record: ToolExecutionRecord) -> ToolExecutionRecord:
    return dataclasses.replace(record, result=copy.deepcopy(record.result))
