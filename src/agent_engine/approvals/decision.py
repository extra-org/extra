"""The typed human decision and the single parsing boundary for it.

Everywhere inside the engine an approval decision is an :class:`ApprovalDecision`
enum value. Free-text values from a UI/API/CLI ("approve", "allow for this
session", "reject", …) are converted to that enum in exactly one place —
:func:`parse_decision` — so no other code compares raw strings.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from agent_engine.approvals.errors import InvalidDecision


class ApprovalDecision(StrEnum):
    """A human's decision about one pending tool invocation.

    * ``ALLOW_ONCE`` — run this invocation only; ask again next time.
    * ``ALLOW_FOR_SESSION`` — run it and stop asking for this tool this session.
    * ``DENY`` — do not run it; store nothing.
    """

    ALLOW_ONCE = "allow_once"
    ALLOW_FOR_SESSION = "allow_for_session"
    DENY = "deny"


# User-facing aliases accepted at the boundary, normalized (lowercased, collapsed
# whitespace) before lookup. Kept intentionally small and explicit.
_ALIASES: dict[str, ApprovalDecision] = {
    "allow_once": ApprovalDecision.ALLOW_ONCE,
    "allow once": ApprovalDecision.ALLOW_ONCE,
    "allowed once": ApprovalDecision.ALLOW_ONCE,
    "allow": ApprovalDecision.ALLOW_ONCE,
    "approve": ApprovalDecision.ALLOW_ONCE,
    "approved": ApprovalDecision.ALLOW_ONCE,
    "allow_for_session": ApprovalDecision.ALLOW_FOR_SESSION,
    "allow for session": ApprovalDecision.ALLOW_FOR_SESSION,
    "allow for this session": ApprovalDecision.ALLOW_FOR_SESSION,
    "allowed for this session": ApprovalDecision.ALLOW_FOR_SESSION,
    "deny": ApprovalDecision.DENY,
    "denied": ApprovalDecision.DENY,
    "reject": ApprovalDecision.DENY,
    "rejected": ApprovalDecision.DENY,
}


def parse_decision(value: Any, *, default: ApprovalDecision | None = None) -> ApprovalDecision:
    """Convert an external decision value into an :class:`ApprovalDecision`.

    Accepts an :class:`ApprovalDecision`, a recognized string alias, or a mapping
    carrying a ``decision`` key (the resume payload shape). Unrecognized input
    returns ``default`` when one is given (fail-closed to ``DENY`` inside the
    runtime), otherwise raises :class:`InvalidDecision` (used at the API boundary
    to reject a bad request).
    """
    if isinstance(value, ApprovalDecision):
        return value
    raw: Any = value.get("decision") if isinstance(value, Mapping) else value
    if isinstance(raw, str):
        normalized = " ".join(raw.strip().lower().split())
        decision = _ALIASES.get(normalized)
        if decision is not None:
            return decision
    if default is not None:
        return default
    raise InvalidDecision(str(raw))
