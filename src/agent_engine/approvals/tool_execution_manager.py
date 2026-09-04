"""Tool-execution idempotency coordination."""

from __future__ import annotations

import hashlib
from typing import Any

from agent_engine.approvals.models import ToolExecutionRecord
from agent_engine.approvals.tool_execution_repository import ToolExecutionRepository


def execution_id_for(tool_call_id: str, *, salt: str = "") -> str:
    """Deterministic idempotency key for one tool call.

    Derived from the stable ``tool_call_id`` so that a retry or a graph re-entry
    which reaches the same tool call computes the same key and can be
    deduplicated. Optional ``salt`` (e.g. an approval id) scopes it further.
    """
    digest = hashlib.sha256(f"{tool_call_id}:{salt}".encode()).hexdigest()
    return f"exec_{digest[:24]}"


class ToolExecutionManager:
    """Idempotency ledger for tool executions.

    Holds no per-run mutable state; safe to share across concurrent runs. The
    repository is injected (Dependency Inversion) and defaults to ``None`` for
    callers that do not need deduplication.
    """

    def __init__(self, *, execution_repository: ToolExecutionRepository | None = None) -> None:
        self._executions = execution_repository

    async def already_executed(self, execution_id: str) -> ToolExecutionRecord | None:
        if self._executions is None:
            return None
        record = await self._executions.get(execution_id)
        if record is not None and record.status == "succeeded":
            return record
        return None

    async def begin_execution(
        self, execution_id: str, *, tool_call_id: str, run_id: str, tool_name: str
    ) -> bool:
        """Return whether this attempt owns the key and may execute."""
        if self._executions is None:
            return True
        _, created = await self._executions.start(
            ToolExecutionRecord(
                execution_id=execution_id,
                tool_call_id=tool_call_id,
                run_id=run_id,
                tool_name=tool_name,
            )
        )
        return created

    async def finish_execution(
        self,
        execution_id: str,
        *,
        status: str,
        result: str,
        structured: Any | None = None,
        artifact: Any | None = None,
    ) -> None:
        if self._executions is not None:
            await self._executions.complete(
                execution_id,
                status=status,
                result=result,
                structured=structured,
                artifact=artifact,
            )
