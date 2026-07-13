"""The approval policy: does this tool call require a human decision?

The policy is a pure, deterministic function of a small query object. It never
performs I/O, touches a UI, or executes a tool — the coordinator gathers the
inputs (auto mode, session-permission lookup) and acts on the answer.

This is the extension point for a future risk-classification layer: a smarter
policy could inspect the invocation and auto-execute provably read-only tools,
without changing the coordinator, the session store, or the runtime boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_engine.approvals.invocation import ToolInvocation


@dataclass(frozen=True)
class ApprovalQuery:
    """The inputs a policy is allowed to consider.

    ``session_allowed`` is resolved by the coordinator against the session store
    before the policy runs, keeping the policy free of I/O.
    """

    invocation: ToolInvocation
    auto_mode: bool
    session_allowed: bool


@runtime_checkable
class ApprovalPolicy(Protocol):
    def requires_approval(self, query: ApprovalQuery) -> bool:
        """Return True when a human decision is required before executing."""


class DefaultApprovalPolicy:
    """The first-version deterministic policy.

    Approval is required for every tool unless the agent is in auto mode or the
    tool is already approved for this session. No risk classification is
    performed — that is intentionally out of scope for this version.
    """

    def requires_approval(self, query: ApprovalQuery) -> bool:
        if query.auto_mode:
            return False
        # Otherwise approval is required unless the tool is already session-allowed.
        return not query.session_allowed
