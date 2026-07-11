"""Centralized tool-execution and approval-lifecycle managers.

``ToolExecutionManager`` is the single place where the EXECUTE / REQUIRE_APPROVAL
/ DENY decision is made and where ``auto_mode`` and idempotency are applied. Every
tool provider — local or MCP — is routed through it, so there is one consistent
policy path and no provider can bypass the approval layer.

``ApprovalManager`` owns the approval records and the distributed-safe claim used
to resume a run exactly once.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from agent_engine.approvals.contract import ToolContract, unknown_contract
from agent_engine.approvals.decision import ApprovalDecision, RiskAssessment, ToolCall
from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalRunMismatch,
    InvalidStateTransition,
    RunNotFound,
    UnauthorizedApprover,
)
from agent_engine.approvals.models import (
    ApprovalRecord,
    ApprovalStatus,
    RunRecord,
    RunStatus,
    ToolExecutionRecord,
    sanitize_arguments,
)
from agent_engine.approvals.policy import DefaultToolApprovalPolicy, ToolApprovalPolicy
from agent_engine.approvals.repository import (
    ApprovalRepository,
    RunRepository,
    ToolExecutionRepository,
)
from agent_engine.logging_config import log

logger = logging.getLogger(__name__)


def execution_id_for(tool_call_id: str, *, salt: str = "") -> str:
    """Deterministic idempotency key for one tool call.

    Derived from the stable ``tool_call_id`` so that a retry or a graph re-entry
    which reaches the same tool call computes the same key and can be
    deduplicated. Optional ``salt`` (e.g. an approval id) scopes it further.
    """
    digest = hashlib.sha256(f"{tool_call_id}:{salt}".encode()).hexdigest()
    return f"exec_{digest[:24]}"


@dataclass(frozen=True)
class ExecutionVerdict:
    """Final decision for a tool call after policy + ``auto_mode`` are combined."""

    decision: ApprovalDecision
    assessment: RiskAssessment
    auto_mode_applied: bool


class ToolExecutionManager:
    """Applies the approval policy, ``auto_mode``, and idempotency to tool calls.

    Holds no per-run mutable state; safe to share read-only across concurrent
    runs. The policy is injected (Dependency Inversion) so it can be swapped
    without touching this class.
    """

    def __init__(
        self,
        *,
        policy: ToolApprovalPolicy | None = None,
        execution_repository: ToolExecutionRepository | None = None,
    ) -> None:
        self._policy = policy or DefaultToolApprovalPolicy()
        self._executions = execution_repository

    def decide(
        self, call: ToolCall, *, auto_mode: bool, contract: ToolContract | None = None
    ) -> ExecutionVerdict:
        """Return the final decision for ``call``.

        ``auto_mode`` may downgrade REQUIRE_APPROVAL to EXECUTE, but never touches
        DENY — a denied action is denied regardless of ``auto_mode``.
        """
        if contract is None:
            contract = unknown_contract(
                "missing",
                reason="missing tool contract; failing closed",
                trusted=False,
            )
        assessment = self._policy.evaluate(call, contract)
        decision = assessment.decision
        applied = False
        if auto_mode and decision == ApprovalDecision.REQUIRE_APPROVAL:
            decision = ApprovalDecision.EXECUTE
            applied = True
        log(
            logger,
            logging.INFO,
            "tool call evaluated",
            agent=call.agent_id,
            tool=call.tool_name,
            provider=call.provider,
            server=call.server_id,
            category=assessment.category.value,
            decision=decision.value,
            auto_mode=auto_mode,
        )
        return ExecutionVerdict(decision=decision, assessment=assessment, auto_mode_applied=applied)

    async def already_executed(self, execution_id: str) -> ToolExecutionRecord | None:
        """Return a completed prior attempt for this key, if any (idempotency)."""
        if self._executions is None:
            return None
        record = await self._executions.get(execution_id)
        if record is not None and record.status == "succeeded":
            return record
        return None

    async def begin_execution(
        self, execution_id: str, *, tool_call_id: str, run_id: str, tool_name: str
    ) -> bool:
        """Claim the idempotency key. Returns True if this attempt should run,
        False if another attempt already owns it (duplicate)."""
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

    async def finish_execution(self, execution_id: str, *, status: str, result: str) -> None:
        if self._executions is not None:
            await self._executions.complete(execution_id, status=status, result=result)


class ApprovalManager:
    """Owns pending-approval records and the atomic resume claim.

    Enforces the full validation set: the run exists, the approval exists and
    belongs to the run, the caller is authorized, the approval is still pending,
    and the stored tool call matches. Exposes a distributed-safe ``claim`` so two
    pods cannot resume the same approval twice.
    """

    def __init__(
        self, *, run_repository: RunRepository, approval_repository: ApprovalRepository
    ) -> None:
        self._runs = run_repository
        self._approvals = approval_repository

    async def register_run(self, record: RunRecord) -> RunRecord:
        return await self._runs.create(record)

    async def get_run(self, run_id: str) -> RunRecord:
        record = await self._runs.get(run_id)
        if record is None:
            raise RunNotFound(run_id)
        return record

    async def get_run_or_none(self, run_id: str) -> RunRecord | None:
        return await self._runs.get(run_id)

    async def create_pending(
        self,
        *,
        run_id: str,
        thread_id: str,
        approval_id: str,
        agent_id: str,
        tool_name: str,
        tool_call_id: str,
        provider: str,
        assessment: RiskAssessment,
        arguments: dict[str, object],
        server_id: str | None = None,
        auth_ref: str | None = None,
        authorized_user_id: str | None = None,
    ) -> ApprovalRecord:
        """Persist a pending approval and move the run into PENDING_APPROVAL.

        Idempotent by ``tool_call_id``: if the graph node is re-entered on resume
        and calls this again for the same tool call, the existing record is
        returned and the run status is left untouched — so resume never creates a
        duplicate approval or an illegal transition.

        Arguments are sanitized before storage; secrets and auth tokens are never
        written — only ``auth_ref`` (a reference resolved again at resume time).
        """
        existing = await self._approvals.get_by_tool_call(run_id, tool_call_id)
        if existing is not None:
            return existing
        record = ApprovalRecord(
            approval_id=approval_id,
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            provider=provider,  # type: ignore[arg-type]
            category=assessment.category,
            reason=assessment.reason,
            arguments=sanitize_arguments(dict(arguments)),
            server_id=server_id,
            auth_ref=auth_ref,
            authorized_user_id=authorized_user_id,
        )
        await self._approvals.create(record)
        # A run may not have been registered by an external caller; register lazily.
        if await self._runs.get(run_id) is None:
            await self._runs.create(
                RunRecord(
                    run_id=run_id, thread_id=thread_id, system_name="", status=RunStatus.RUNNING
                )
            )
        await self._runs.set_status(run_id, RunStatus.PENDING_APPROVAL)
        log(
            logger,
            logging.INFO,
            "approval required",
            run_id=run_id,
            thread_id=thread_id,
            approval_id=approval_id,
            agent=agent_id,
            tool=tool_name,
            tool_call_id=tool_call_id,
            provider=provider,
            category=assessment.category.value,
        )
        return record

    async def get_pending(self, run_id: str) -> ApprovalRecord | None:
        return await self._approvals.get_pending_for_run(run_id)

    async def _load_for_run(self, run_id: str, approval_id: str) -> ApprovalRecord:
        await self.get_run(run_id)  # raises RunNotFound
        record = await self._approvals.get(approval_id)
        if record is None:
            from agent_engine.approvals.errors import ApprovalNotFound

            raise ApprovalNotFound(approval_id)
        if record.run_id != run_id:
            raise ApprovalRunMismatch(approval_id, run_id)
        return record

    async def get_approval(self, run_id: str, approval_id: str) -> ApprovalRecord:
        return await self._load_for_run(run_id, approval_id)

    async def claim(
        self,
        *,
        run_id: str,
        approval_id: str,
        caller_user_id: str | None = None,
    ) -> ApprovalRecord:
        """Validate and atomically claim an approval for resume.

        Exactly one concurrent caller succeeds; the rest get
        :class:`ApprovalAlreadyProcessed`. On success the run and approval both
        move to RESUMING.
        """
        record = await self._load_for_run(run_id, approval_id)
        if record.authorized_user_id is not None and record.authorized_user_id != caller_user_id:
            raise UnauthorizedApprover(approval_id)
        try:
            claimed = await self._approvals.claim(approval_id)
        except InvalidStateTransition as exc:
            log(
                logger,
                logging.INFO,
                "duplicate resume prevented",
                run_id=run_id,
                approval_id=approval_id,
                status=record.status.value,
            )
            raise ApprovalAlreadyProcessed(approval_id, record.status.value) from exc
        # Move the run in lock-step; RESUMING is only reachable from PENDING_APPROVAL.
        run = await self.get_run(run_id)
        if run.status == RunStatus.PENDING_APPROVAL:
            await self._runs.set_status(run_id, RunStatus.RESUMING)
        log(
            logger,
            logging.INFO,
            "resume claimed",
            run_id=run_id,
            approval_id=approval_id,
            tool_call_id=claimed.tool_call_id,
        )
        return claimed

    async def finalize(self, approval_id: str, *, approved: bool) -> ApprovalRecord:
        target = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        return await self._approvals.set_status(approval_id, target)

    async def mark_run(self, run_id: str, status: RunStatus) -> RunRecord:
        return await self._runs.set_status(run_id, status)
