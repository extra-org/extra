"""Optional engine capability for streaming an existing HITL run."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.runtime.hooks.models import RunContext
from agent_engine.runtime.streaming import RunStreamEvent


@runtime_checkable
class ApprovalStreamingEngine(Protocol):
    def resume_stream(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
        access_token: str | None = None,
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]: ...
