"""Typed errors for the approval lifecycle.

All approval failures derive from :class:`ApprovalError` so the API layer can map
the whole family to stable HTTP responses without leaking internals. Each error
carries only safe identifiers — never tool arguments, tokens, or stack traces.
"""

from __future__ import annotations


class ApprovalError(Exception):
    """Base class for every approval-lifecycle failure."""


class RunNotFound(ApprovalError):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run not found: {run_id}")


class ApprovalNotFound(ApprovalError):
    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__(f"approval not found: {approval_id}")


class ApprovalRunMismatch(ApprovalError):
    """The approval exists but does not belong to the supplied run."""

    def __init__(self, approval_id: str, run_id: str) -> None:
        self.approval_id = approval_id
        self.run_id = run_id
        super().__init__(f"approval {approval_id} does not belong to run {run_id}")


class ApprovalAlreadyProcessed(ApprovalError):
    """The approval was already approved/rejected or is mid-resume.

    Raised for the double-click / retry case so callers get a stable, explicit
    signal instead of a duplicated tool execution.
    """

    def __init__(self, approval_id: str, status: str) -> None:
        self.approval_id = approval_id
        self.status = status
        super().__init__(f"approval {approval_id} already processed (status={status})")


class UnauthorizedApprover(ApprovalError):
    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__(f"caller is not authorized to decide approval {approval_id}")


class InvalidDecision(ApprovalError):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"invalid approval decision: {value!r}")


class InvalidStateTransition(ApprovalError):
    def __init__(self, entity: str, from_state: str, to_state: str) -> None:
        self.entity = entity
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"invalid {entity} transition: {from_state} -> {to_state}")


class ToolDenied(ApprovalError):
    """The policy classified the call as ``DENY``; it must never execute."""

    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"tool {tool_name!r} denied: {reason}")


class ToolNoLongerExists(ApprovalError):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"tool no longer exists: {tool_name}")


class CheckpointNotFound(ApprovalError):
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"no checkpoint found for thread: {thread_id}")
