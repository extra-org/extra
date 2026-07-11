"""Core value objects for the Human-in-the-Loop tool-approval layer.

These are pure domain types with no runtime, LangGraph, or MCP imports. They are
the vocabulary shared by the policy engine, the execution manager, and the
approval repositories, so every tool provider is evaluated the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_engine.runtime.tool_models import ToolProviderName


class ApprovalDecision(StrEnum):
    """The three outcomes the engine may produce for a requested tool call.

    * ``EXECUTE`` — run the tool now (no human interaction).
    * ``REQUIRE_APPROVAL`` — pause the graph and wait for a human decision.
    * ``DENY`` — never run the tool; ``auto_mode`` must not bypass this.
    """

    EXECUTE = "execute"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class RiskCategory(StrEnum):
    """Coarse behavioral classification of a tool call.

    Deliberately provider-agnostic: an MCP tool and a local tool that both
    "delete" something land in the same category. ``UNKNOWN`` is the conservative
    fallback for anything the classifier cannot confidently place.
    """

    READ = "read"
    DRAFT = "draft"
    SEND = "send"
    WRITE = "write"
    DELETE = "delete"
    FINANCIAL = "financial"
    ACCESS_CONTROL = "access_control"
    CODE_EXECUTION = "code_execution"
    CONDITIONAL = "conditional"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ToolCall:
    """A concrete, about-to-run tool invocation handed to the policy engine.

    Carries the concrete invocation metadata. Runtime approval is based on a
    cached discovery-time ``ToolContract``; ``arguments`` are only used by
    contracts that declare an explicit conditional discriminator path.
    """

    tool_name: str
    agent_id: str
    provider: ToolProviderName = "local"
    server_id: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskAssessment:
    """The pure-risk verdict for a :class:`ToolCall`.

    ``decision`` here is the *policy* decision before ``auto_mode`` is applied —
    ``auto_mode`` handling is the execution manager's responsibility, not the
    policy's, so the policy stays a deterministic pure function of the call.
    """

    decision: ApprovalDecision
    category: RiskCategory
    reason: str
