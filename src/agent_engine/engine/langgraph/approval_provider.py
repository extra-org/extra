"""LangGraph-backed approval provider.

The concrete :class:`ApprovalProvider` for this runtime: it suspends the run at a
LangGraph ``interrupt`` so a human can decide out-of-band (e.g. over HTTP), then
returns their typed decision when the run resumes. Keeping this here — not in the
``approvals`` package — is what lets the approval core stay free of any UI or
LangGraph import (Dependency Inversion).
"""

from __future__ import annotations

import logging
import uuid

from langgraph.types import interrupt

from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.manager import ApprovalManager
from agent_engine.approvals.provider import ApprovalRequest
from agent_engine.logging_config import log
from agent_engine.runtime.hooks import current_run_context

logger = logging.getLogger(__name__)


class InterruptApprovalProvider:
    """Requests a human decision by interrupting the LangGraph run.

    Persists a sanitized pending :class:`ApprovalRecord` (so the HTTP API can
    list it and resume it), then calls ``interrupt`` to checkpoint the run and
    hand control back to the caller. On resume the node re-executes and
    ``interrupt`` returns the resume payload, which is parsed into a typed
    :class:`ApprovalDecision`. An unrecognized payload fails closed to ``DENY``,
    so an ambiguous resume never triggers a side effect.
    """

    def __init__(self, approval_manager: ApprovalManager) -> None:
        self._approvals = approval_manager

    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        inv = request.invocation
        run_context = current_run_context.get()
        run_id = run_context.run_id if run_context and run_context.run_id else inv.tool_call_id
        authorized_user_id = run_context.user_id if run_context else None
        auth_ref = run_context.conversation_id if run_context else None
        approval_id = f"appr_{uuid.uuid4().hex[:16]}"

        record = await self._approvals.create_pending(
            run_id=run_id,
            thread_id=run_id,
            approval_id=approval_id,
            agent_id=inv.agent_id,
            tool_name=inv.tool_name,
            tool_call_id=inv.tool_call_id,
            provider=inv.provider,
            description=request.description,
            arguments=request.masked_arguments,
            server_id=inv.server_id,
            auth_ref=auth_ref,
            authorized_user_id=authorized_user_id,
        )

        payload = {
            "type": "tool_approval",
            "approval_id": record.approval_id,
            "run_id": run_id,
            "agent_id": inv.agent_id,
            "tool_name": inv.tool_name,
            "provider": inv.provider,
            "server_id": inv.server_id,
            "description": request.description,
            "arguments": dict(request.masked_arguments),
        }
        log(
            logger,
            logging.INFO,
            "run interrupted",
            run_id=run_id,
            approval_id=record.approval_id,
            tool=inv.tool_name,
            tool_call_id=inv.tool_call_id,
        )
        resume = interrupt(payload)
        return parse_decision(resume, default=ApprovalDecision.DENY)
