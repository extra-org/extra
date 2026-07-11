"""Run / approval / execution domain model.

Keeps the five identifiers the design mandates strictly separate:

* ``run_id``       — business-level Extra run identifier.
* ``thread_id``    — LangGraph persistence / checkpoint identifier.
* ``approval_id``  — one pending approval request.
* ``tool_call_id`` — the agent's requested tool call.
* ``execution_id`` — a single actual execution attempt (idempotency key).

State transitions are explicit and validated; illegal transitions raise so a
completed or rejected run can never silently re-run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_engine.approvals.decision import RiskCategory
from agent_engine.approvals.errors import InvalidStateTransition
from agent_engine.runtime.tool_models import ToolProviderName


class RunStatus(StrEnum):
    RUNNING = "running"
    PENDING_APPROVAL = "pending_approval"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    RESUMING = "resuming"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecisionKind(StrEnum):
    """The human's decision. ``EDIT_AND_APPROVE`` is reserved so it can be added
    later (with edited arguments) without reshaping the model."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT_AND_APPROVE = "edit_and_approve"


# Allowed forward transitions. Anything not listed is rejected. Note there is no
# path out of COMPLETED/FAILED and no REJECTED -> APPROVED, per the spec.
_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RUNNING: frozenset(
        {RunStatus.PENDING_APPROVAL, RunStatus.COMPLETED, RunStatus.FAILED}
    ),
    RunStatus.PENDING_APPROVAL: frozenset({RunStatus.RESUMING, RunStatus.FAILED}),
    RunStatus.RESUMING: frozenset(
        {RunStatus.RUNNING, RunStatus.PENDING_APPROVAL, RunStatus.COMPLETED, RunStatus.FAILED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
}

_APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset({ApprovalStatus.RESUMING, ApprovalStatus.REJECTED}),
    ApprovalStatus.RESUMING: frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
}


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in _RUN_TRANSITIONS[current]:
        raise InvalidStateTransition("run", current.value, target.value)


def ensure_approval_transition(current: ApprovalStatus, target: ApprovalStatus) -> None:
    if target not in _APPROVAL_TRANSITIONS[current]:
        raise InvalidStateTransition("approval", current.value, target.value)


# Argument keys that must never be persisted or returned to a UI. Matched
# case-insensitively as a substring of the key.
_SENSITIVE_ARG_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "session",
    "cookie",
    "bearer",
)
_REDACTED = "***redacted***"


def sanitize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with obviously-sensitive values redacted.

    Used before persisting an approval or exposing arguments to the UI. It is a
    defense-in-depth safeguard, not a substitute for keeping credentials out of
    tool arguments entirely.
    """
    clean: dict[str, Any] = {}
    for key, value in arguments.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in _SENSITIVE_ARG_MARKERS):
            clean[key] = _REDACTED
        else:
            clean[key] = value
    return clean


@dataclass
class RunRecord:
    """Business-level state of one run and its LangGraph thread binding."""

    run_id: str
    thread_id: str
    system_name: str
    status: RunStatus = RunStatus.RUNNING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def transition(self, target: RunStatus) -> None:
        ensure_run_transition(self.status, target)
        self.status = target
        self.updated_at = time.time()


@dataclass
class ApprovalRecord:
    """One pending approval. Holds only sanitized, non-secret data.

    Credentials are never stored here: only a reference to the authorization
    context (``auth_ref``) is kept, and valid credentials are resolved again at
    resume time.
    """

    approval_id: str
    run_id: str
    thread_id: str
    agent_id: str
    tool_name: str
    tool_call_id: str
    provider: ToolProviderName
    category: RiskCategory
    reason: str
    arguments: dict[str, Any]  # already sanitized by the caller
    status: ApprovalStatus = ApprovalStatus.PENDING
    server_id: str | None = None
    auth_ref: str | None = None
    authorized_user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def transition(self, target: ApprovalStatus) -> None:
        ensure_approval_transition(self.status, target)
        self.status = target
        self.updated_at = time.time()


@dataclass
class ToolExecutionRecord:
    """Idempotency ledger entry for one actual execution attempt.

    Keyed by ``execution_id`` (derived from the stable ``tool_call_id``), it lets
    the engine detect and short-circuit a duplicate execution of the same tool
    call — the core protection against double side effects on retry/re-entry.
    """

    execution_id: str
    tool_call_id: str
    run_id: str
    tool_name: str
    status: str = "started"  # started | succeeded | failed
    result: str | None = None
    created_at: float = field(default_factory=time.time)
