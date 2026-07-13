"""Coordinates the approval workflow for one tool invocation.

The coordinator wires the policy, the session store, and the approval provider
together. It contains no tool-specific logic and performs no UI interaction (the
injected provider owns that). Its single entry point is :meth:`resolve`:

1. Ask the policy whether approval is required (auto mode → session → ask).
2. If not required, permit execution immediately (the provider is never called).
3. Otherwise request a typed decision from the provider.
4. Apply the decision: store a session permission only for ``ALLOW_FOR_SESSION``
   and report whether the invocation may execute.

The runtime fails closed: a missing session id yields no session key, so no
session permission can match or be stored, and every value except an explicit
allow keeps the tool from running.
"""

from __future__ import annotations

import logging

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.invocation import ToolInvocation
from agent_engine.approvals.policy import (
    ApprovalPolicy,
    ApprovalQuery,
    DefaultApprovalPolicy,
)
from agent_engine.approvals.provider import (
    ApprovalOutcome,
    ApprovalProvider,
    ApprovalRequest,
)
from agent_engine.approvals.sanitization import mask_arguments
from agent_engine.approvals.session_store import (
    InMemorySessionApprovalStore,
    SessionApprovalStore,
)
from agent_engine.logging_config import log

logger = logging.getLogger(__name__)

# Reasons attached to an ApprovalOutcome for logs/telemetry only — never used for
# control flow. Named here so the closed set is explicit and greppable.
_REASON_AUTO_MODE = "auto_mode"
_REASON_SESSION_ALLOWED = "session_allowed"
_REASON_USER_DECISION = "user_decision"


class ApprovalCoordinator:
    """Applies the approval policy, session permissions, and provider to a call.

    Holds no per-invocation mutable state, so one instance is shared across
    concurrent runs; the session store it depends on is itself async-safe. All
    three collaborators are injected (Dependency Inversion) and can be swapped
    without touching this class.
    """

    def __init__(
        self,
        provider: ApprovalProvider,
        *,
        policy: ApprovalPolicy | None = None,
        session_store: SessionApprovalStore | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy or DefaultApprovalPolicy()
        self._sessions = session_store or InMemorySessionApprovalStore()

    async def resolve(self, invocation: ToolInvocation, *, auto_mode: bool) -> ApprovalOutcome:
        query = await self._build_query(invocation, auto_mode=auto_mode)
        required = self._policy.requires_approval(query)
        self._log_evaluated(query, required=required)

        if not required:
            reason = _REASON_AUTO_MODE if auto_mode else _REASON_SESSION_ALLOWED
            return ApprovalOutcome(execute=True, reason=reason)

        decision = await self._provider.request_decision(self._build_request(invocation))
        execute = await self._apply(invocation, decision)
        return ApprovalOutcome(execute=execute, reason=_REASON_USER_DECISION, decision=decision)

    async def _build_query(self, invocation: ToolInvocation, *, auto_mode: bool) -> ApprovalQuery:
        """Gather the inputs the policy may consider (the session lookup is here,
        so the policy itself stays a pure, synchronous function)."""
        session_allowed = await self._session_allowed(invocation)
        return ApprovalQuery(
            invocation=invocation, auto_mode=auto_mode, session_allowed=session_allowed
        )

    def _build_request(self, invocation: ToolInvocation) -> ApprovalRequest:
        """Build the human-facing request with sensitive arguments masked.

        ``mask_arguments`` returns a copy, so the arguments later handed to the
        tool are never mutated.
        """
        return ApprovalRequest(
            invocation=invocation,
            description=invocation.description,
            masked_arguments=mask_arguments(invocation.arguments),
        )

    async def _session_allowed(self, invocation: ToolInvocation) -> bool:
        key = invocation.session_key
        if key is None:
            return False
        return await self._sessions.is_allowed(key)

    async def _apply(self, invocation: ToolInvocation, decision: ApprovalDecision) -> bool:
        """Apply a typed decision; return whether the invocation may execute.

        Only ``ALLOW_FOR_SESSION`` persists a permission, and only when a session
        key exists. ``DENY`` runs nothing and stores nothing.
        """
        if decision == ApprovalDecision.DENY:
            log(
                logger,
                logging.INFO,
                "tool denied by user",
                agent=invocation.agent_id,
                tool=invocation.tool_name,
                tool_call_id=invocation.tool_call_id,
            )
            return False
        if decision == ApprovalDecision.ALLOW_FOR_SESSION:
            key = invocation.session_key
            if key is not None:
                await self._sessions.allow(key)
                log(
                    logger,
                    logging.INFO,
                    "session approval stored",
                    agent=invocation.agent_id,
                    tool=invocation.tool_name,
                    tool_identity=invocation.tool_identity,
                )
        return True

    def _log_evaluated(self, query: ApprovalQuery, *, required: bool) -> None:
        invocation = query.invocation
        log(
            logger,
            logging.INFO,
            "approval evaluated",
            agent=invocation.agent_id,
            tool=invocation.tool_name,
            provider=invocation.provider,
            server=invocation.server_id,
            tool_call_id=invocation.tool_call_id,
            auto_mode=query.auto_mode,
            session_allowed=query.session_allowed,
            requires_approval=required,
        )
