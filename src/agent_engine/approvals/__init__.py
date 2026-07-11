"""Human-in-the-Loop tool-approval subsystem.

A general engine capability applied consistently to every tool provider (local
and MCP). The centralized :class:`ToolExecutionManager` produces one of three
decisions — EXECUTE / REQUIRE_APPROVAL / DENY — for every tool call before any
provider is invoked.
"""

from __future__ import annotations

from agent_engine.approvals.contract import (
    AUTO_EXECUTE_CONFIDENCE_THRESHOLD,
    LOCAL_SERVER_IDENTITY,
    ConditionalEffect,
    MCPServerIdentity,
    MCPToolDefinition,
    ToolContract,
    ToolContractSource,
    unknown_contract,
)
from agent_engine.approvals.contract_registry import (
    InMemoryToolContractRegistry,
    ToolContractRegistry,
)
from agent_engine.approvals.contract_service import ToolContractService
from agent_engine.approvals.decision import (
    ApprovalDecision,
    RiskAssessment,
    RiskCategory,
    ToolCall,
)
from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRunMismatch,
    CheckpointNotFound,
    InvalidDecision,
    InvalidStateTransition,
    RunNotFound,
    ToolDenied,
    ToolNoLongerExists,
    UnauthorizedApprover,
)
from agent_engine.approvals.manager import (
    ApprovalManager,
    ExecutionVerdict,
    ToolExecutionManager,
    execution_id_for,
)
from agent_engine.approvals.models import (
    ApprovalDecisionKind,
    ApprovalRecord,
    ApprovalStatus,
    RunRecord,
    RunStatus,
    ToolExecutionRecord,
    sanitize_arguments,
)
from agent_engine.approvals.policy import (
    DefaultToolApprovalPolicy,
    ToolApprovalPolicy,
)
from agent_engine.approvals.repository import (
    ApprovalRepository,
    InMemoryApprovalRepository,
    InMemoryRunRepository,
    InMemoryToolExecutionRepository,
    RunRepository,
    ToolExecutionRepository,
)

__all__ = [
    "AUTO_EXECUTE_CONFIDENCE_THRESHOLD",
    "LOCAL_SERVER_IDENTITY",
    "ApprovalAlreadyProcessed",
    "ApprovalDecision",
    "ApprovalDecisionKind",
    "ApprovalError",
    "ApprovalManager",
    "ApprovalNotFound",
    "ApprovalRecord",
    "ApprovalRepository",
    "ApprovalRunMismatch",
    "ApprovalStatus",
    "CheckpointNotFound",
    "ConditionalEffect",
    "DefaultToolApprovalPolicy",
    "ExecutionVerdict",
    "InMemoryApprovalRepository",
    "InMemoryRunRepository",
    "InMemoryToolContractRegistry",
    "InMemoryToolExecutionRepository",
    "InvalidDecision",
    "InvalidStateTransition",
    "MCPServerIdentity",
    "MCPToolDefinition",
    "RiskAssessment",
    "RiskCategory",
    "RunNotFound",
    "RunRecord",
    "RunRepository",
    "RunStatus",
    "ToolApprovalPolicy",
    "ToolCall",
    "ToolContract",
    "ToolContractRegistry",
    "ToolContractService",
    "ToolContractSource",
    "ToolDenied",
    "ToolExecutionManager",
    "ToolExecutionRecord",
    "ToolExecutionRepository",
    "ToolNoLongerExists",
    "UnauthorizedApprover",
    "execution_id_for",
    "sanitize_arguments",
    "unknown_contract",
]
