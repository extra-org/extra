"""Human-in-the-Loop tool-approval subsystem.

A deterministic approval layer applied consistently to every tool provider
(local and MCP) before any tool executes. There is intentionally **no** LLM-based
risk classification: by default every tool call requires explicit human approval,
unless the agent runs in auto mode or the tool was already approved for the
current session.

Responsibilities are split into small, single-purpose pieces:

* :mod:`decision` — the typed :class:`ApprovalDecision` and the one parsing boundary.
* :mod:`approval_policy` — the pure "does this need approval?" rule (the future extension
  point for a risk-classification policy).
* :mod:`invocation` — value objects describing a concrete pending tool call.
* :mod:`identity` — a stable, collision-safe tool identity.
* :mod:`session_approval_repository` — where session permissions live.
* :mod:`approval_provider` — the frontend-agnostic approval-provider abstraction.
* :mod:`coordinator` — wires policy + session store + provider together.
* :mod:`sanitization` — masks sensitive arguments before display/persistence.

The manager, model, and repository modules carry the run/approval resume
lifecycle and execution idempotency; they hold no classification logic.
"""

from __future__ import annotations

from agent_engine.approvals.approval_manager import ApprovalManager
from agent_engine.approvals.approval_policy import ApprovalPolicy, ApprovalQuery
from agent_engine.approvals.approval_provider import (
    ApprovalOutcome,
    ApprovalProvider,
    ApprovalRequest,
)
from agent_engine.approvals.approval_repository import ApprovalRepository
from agent_engine.approvals.coordinator import ApprovalCoordinator
from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.default_approval_policy import DefaultApprovalPolicy
from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRunMismatch,
    CheckpointNotFound,
    InvalidDecision,
    InvalidStateTransition,
    RunNotFound,
    ToolNoLongerExists,
    UnauthorizedApprover,
)
from agent_engine.approvals.identity import tool_identity
from agent_engine.approvals.in_memory_approval_repository import InMemoryApprovalRepository
from agent_engine.approvals.in_memory_session_approval_repository import (
    InMemorySessionApprovalRepository,
    InMemorySessionApprovalStore,
)
from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.invocation import (
    SessionApprovalGrant,
    SessionApprovalKey,
    SessionApprovalScope,
    ToolInvocation,
)
from agent_engine.approvals.models import (
    ApprovalRecord,
    ApprovalStatus,
    RunRecord,
    RunStatus,
    ToolExecutionRecord,
    ToolExecutionStatus,
)
from agent_engine.approvals.sanitization import mask_arguments, mask_sensitive
from agent_engine.approvals.session_approval_repository import SessionApprovalRepository
from agent_engine.approvals.session_approval_store import SessionApprovalStore
from agent_engine.approvals.tool_execution_manager import (
    ToolExecutionClaim,
    ToolExecutionManager,
    ToolExecutionStateError,
    execution_id_for,
)
from agent_engine.approvals.tool_execution_repository import ToolExecutionRepository

__all__ = [
    "ApprovalAlreadyProcessed",
    "ApprovalCoordinator",
    "ApprovalDecision",
    "ApprovalError",
    "ApprovalManager",
    "ApprovalNotFound",
    "ApprovalOutcome",
    "ApprovalPolicy",
    "ApprovalProvider",
    "ApprovalQuery",
    "ApprovalRecord",
    "ApprovalRepository",
    "ApprovalRequest",
    "ApprovalRunMismatch",
    "ApprovalStatus",
    "CheckpointNotFound",
    "DefaultApprovalPolicy",
    "InMemoryApprovalRepository",
    "InMemorySessionApprovalRepository",
    "InMemorySessionApprovalStore",
    "InMemoryToolExecutionRepository",
    "InvalidDecision",
    "InvalidStateTransition",
    "RunNotFound",
    "RunRecord",
    "RunStatus",
    "SessionApprovalGrant",
    "SessionApprovalKey",
    "SessionApprovalRepository",
    "SessionApprovalScope",
    "SessionApprovalStore",
    "ToolExecutionClaim",
    "ToolExecutionManager",
    "ToolExecutionRecord",
    "ToolExecutionRepository",
    "ToolExecutionStateError",
    "ToolExecutionStatus",
    "ToolInvocation",
    "ToolNoLongerExists",
    "UnauthorizedApprover",
    "execution_id_for",
    "mask_arguments",
    "mask_sensitive",
    "parse_decision",
    "tool_identity",
]
