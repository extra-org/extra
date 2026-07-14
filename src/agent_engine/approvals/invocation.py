"""Value objects describing a concrete tool invocation awaiting a decision.

These are pure domain types — no LangGraph, MCP-client, or LLM imports — shared
by the policy, coordinator, session store, and approval provider so every tool
call is evaluated the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_engine.approvals.identity import tool_identity
from agent_engine.runtime.tool_models import ToolProviderName


@dataclass(frozen=True)
class SessionApprovalKey:
    """The scope of a single "allow for this session" permission.

    Frozen (and therefore hashable) so it can key a set/dict store directly.
    Optional identity dimensions use empty strings rather than ``None`` so
    persistent adapters can enforce one collision-safe uniqueness constraint.
    Argument values are deliberately excluded: ``ALLOW_FOR_SESSION`` applies
    to the tool identity, not one argument set.
    """

    session_id: str
    agent_id: str
    tool_identity: str
    system_namespace: str = ""
    user_id: str = ""
    organization_id: str = ""

    @property
    def scope(self) -> SessionApprovalScope:
        return SessionApprovalScope(
            session_id=self.session_id,
            system_namespace=self.system_namespace,
            user_id=self.user_id,
            organization_id=self.organization_id,
        )


@dataclass(frozen=True)
class SessionApprovalScope:
    """Identity shared by every permission in one logical session."""

    session_id: str
    system_namespace: str = ""
    user_id: str = ""
    organization_id: str = ""


@dataclass(frozen=True)
class SessionApprovalGrant:
    """Optional provenance and lifecycle metadata for a permission grant."""

    run_id: str | None = None
    approval_id: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ToolInvocation:
    """One concrete, about-to-run tool call handed to the approval workflow.

    ``tool_call_id`` uniquely identifies this pending invocation so a decision
    can never be matched to a different call. ``session_id`` is the logical
    runtime session (see the session store docs); it is ``None`` when the caller
    supplied no session, in which case no session permission can be granted.
    """

    tool_call_id: str
    agent_id: str
    tool_name: str
    session_id: str | None = None
    provider: ToolProviderName = "local"
    server_id: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    system_namespace: str = ""
    user_id: str = ""
    organization_id: str = ""
    run_id: str | None = None
    approval_id: str | None = None

    @property
    def tool_identity(self) -> str:
        return tool_identity(
            provider=self.provider, server_id=self.server_id, tool_name=self.tool_name
        )

    @property
    def session_key(self) -> SessionApprovalKey | None:
        """The session-permission key, or ``None`` when there is no session.

        Returning ``None`` for a missing session id is what makes the runtime
        fail closed: without a key the coordinator can neither find nor store a
        session permission, so approval is required every time.
        """
        if not self.session_id:
            return None
        return SessionApprovalKey(
            session_id=self.session_id,
            agent_id=self.agent_id,
            tool_identity=self.tool_identity,
            system_namespace=self.system_namespace,
            user_id=self.user_id,
            organization_id=self.organization_id,
        )

    @property
    def description(self) -> str:
        """A one-line, human-readable summary for the approval prompt.

        States plainly that the tool has not run yet, so the approver understands
        they are authorizing a pending action rather than reviewing a past one.
        """
        where = f" on server '{self.server_id}'" if self.server_id else ""
        return (
            f"Agent '{self.agent_id}' wants to call tool '{self.tool_name}'"
            f"{where}. It has NOT been executed yet and needs your approval."
        )
