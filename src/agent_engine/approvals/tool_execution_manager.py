"""Tool-execution idempotency coordination."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agent_engine.approvals.models import ToolExecutionRecord, ToolExecutionStatus
from agent_engine.approvals.tool_execution_repository import ToolExecutionRepository
from agent_engine.runtime.tool_results import NormalizedToolResult, ToolResultValidationError


def execution_id_for(tool_call_id: str, *, salt: str = "") -> str:
    """Deterministic idempotency key for one tool call.

    Derived from the stable ``tool_call_id`` so that a retry or a graph re-entry
    which reaches the same tool call computes the same key and can be
    deduplicated. Optional ``salt`` (e.g. an approval id) scopes it further.
    """
    digest = hashlib.sha256(f"{tool_call_id}:{salt}".encode()).hexdigest()
    return f"exec_{digest[:24]}"


class ToolExecutionStateError(RuntimeError):
    """The execution ledger contains an invalid or incomplete terminal row."""


@dataclass(frozen=True)
class ToolExecutionClaim:
    """Whether this caller owns execution or must replay a terminal result."""

    should_execute: bool
    status: ToolExecutionStatus | None = None
    result: NormalizedToolResult | None = None

    def __post_init__(self) -> None:
        if self.should_execute:
            if self.status is not None or self.result is not None:
                raise ValueError("an execution owner cannot already have a terminal result")
            return
        if self.status not in ("succeeded", "failed") or self.result is None:
            raise ValueError("a replay claim must contain one terminal result")


class ToolExecutionManager:
    """Idempotency ledger for tool executions.

    Holds no per-run mutable state; safe to share across concurrent runs. The
    repository is injected (Dependency Inversion) and defaults to ``None`` for
    callers that do not need deduplication.
    """

    def __init__(self, *, execution_repository: ToolExecutionRepository | None = None) -> None:
        self._executions = execution_repository

    async def already_executed(self, execution_id: str) -> ToolExecutionRecord | None:
        """Return the successful ledger row (backwards-compatible API)."""
        if self._executions is None:
            return None
        record = await self._executions.get(execution_id)
        if record is not None and record.status == "succeeded":
            return record
        return None

    async def restored_result(self, execution_id: str) -> NormalizedToolResult | None:
        """Return a successful execution through Extra's current result model."""
        record = await self.already_executed(execution_id)
        return _restore_result(record) if record is not None else None

    async def begin_execution(
        self, execution_id: str, *, tool_call_id: str, run_id: str, tool_name: str
    ) -> bool:
        """Create an execution row without waiting (legacy coordination API)."""
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

    async def claim_execution(
        self, execution_id: str, *, tool_call_id: str, run_id: str, tool_name: str
    ) -> ToolExecutionClaim:
        """Atomically claim a call or wait for its current owner to finish."""
        if self._executions is None:
            return ToolExecutionClaim(should_execute=True)
        record, created = await self._executions.start(
            ToolExecutionRecord(
                execution_id=execution_id,
                tool_call_id=tool_call_id,
                run_id=run_id,
                tool_name=tool_name,
            )
        )
        if created:
            return ToolExecutionClaim(should_execute=True)
        if record.status == "started":
            record = await self._executions.wait_for_completion(execution_id)
        return ToolExecutionClaim(
            should_execute=False,
            status=record.status,
            result=_restore_result(record),
        )

    async def finish_execution(
        self,
        execution_id: str,
        *,
        status: ToolExecutionStatus,
        result: NormalizedToolResult | str,
    ) -> None:
        if status == "started":
            raise ValueError("finished tool execution must be terminal")
        if self._executions is not None:
            normalized = (
                result
                if isinstance(result, NormalizedToolResult)
                else NormalizedToolResult.text_only(result)
            )
            await self._executions.complete(
                execution_id,
                status=status,
                result=normalized.to_persisted(),
            )


def _restore_result(record: ToolExecutionRecord) -> NormalizedToolResult:
    if record.status == "started" or record.result is None:
        raise ToolExecutionStateError(
            f"tool execution {record.execution_id} has no terminal result"
        )
    try:
        return NormalizedToolResult.from_persisted(record.result)
    except ToolResultValidationError as exc:
        raise ToolExecutionStateError(
            f"tool execution {record.execution_id} contains an invalid result"
        ) from exc
