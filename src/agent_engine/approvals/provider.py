"""The approval provider abstraction and the request/outcome value objects.

Requesting a decision from a human is a frontend concern. The engine depends
only on the :class:`ApprovalProvider` protocol; a concrete implementation (a
LangGraph interrupt, a CLI prompt, an HTTP round-trip, …) lives in the runtime
layer. This inverts the dependency so the approval package never imports a UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.invocation import ToolInvocation


@dataclass(frozen=True)
class ApprovalRequest:
    """Everything a human needs to decide, with secrets already masked.

    ``masked_arguments`` is the redacted view built by the sanitizer; the raw
    arguments never appear here. ``description`` states plainly that the tool has
    *not* run yet, so the approver knows they are authorizing a future action.
    """

    invocation: ToolInvocation
    description: str
    masked_arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ApprovalOutcome:
    """The coordinator's verdict for one invocation.

    ``execute`` is the only thing the tool-execution boundary must honour.
    ``decision`` is ``None`` when approval was not required (auto mode or an
    existing session permission); ``reason`` is a short, log-safe explanation.
    """

    execute: bool
    reason: str
    decision: ApprovalDecision | None = None


@runtime_checkable
class ApprovalProvider(Protocol):
    async def request_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        """Present the request to a human and return their typed decision.

        Implementations must never execute the tool. Raising (other than a
        runtime control-flow signal such as a LangGraph interrupt) is treated by
        the coordinator as a failure to obtain consent, so the tool does not run.
        """
