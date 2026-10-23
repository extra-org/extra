"""Optional engine capability for inspecting and resuming HITL runs."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.engine.types import PendingApproval, RunResult
from agent_engine.runtime.hooks.models import RunContext


@runtime_checkable
class ApprovalEngine(Protocol):
    async def get_pending_approval(self, run_id: str) -> PendingApproval | None: ...

    async def get_processed_result(
        self,
        run_id: str,
        approval_id: str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
    ) -> RunResult | None: ...

    async def resume(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
        access_token: str | None = None,
        context: RunContext | None = None,
    ) -> RunResult: ...
