from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from agent_engine.approvals.invocation import (
    SessionApprovalGrant,
    SessionApprovalKey,
)
from agent_engine.approvals.session_store import InMemorySessionApprovalRepository

EventSink = Callable[[str], None]


class ObservableSessionApprovalRepository(InMemorySessionApprovalRepository):
    """In-memory approvals with safe terminal events for the runnable demo.

    Events contain only the logical session ID and provider-qualified tool
    identity. Arguments, headers, credentials, grant metadata, and secret values
    are deliberately omitted.
    """

    def __init__(
        self,
        *,
        emit: EventSink = print,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(clock=clock)
        self._emit = emit

    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        allowed = await super().is_allowed(key)
        self._emit(
            f"[SESSION CACHE] session_id={key.session_id} tool={key.tool_identity} "
            f"source=session_cache hit={str(allowed).lower()}"
        )
        return allowed

    async def allow(
        self,
        key: SessionApprovalKey,
        *,
        grant: SessionApprovalGrant | None = None,
    ) -> None:
        await super().allow(key, grant=grant)
        self._emit(
            f"[APPROVAL STORED] session_id={key.session_id} tool={key.tool_identity} "
            "decision=allow_for_session"
        )
