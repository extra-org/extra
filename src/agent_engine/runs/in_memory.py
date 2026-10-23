"""Process-local run repository for development and tests."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from agent_engine.approvals.errors import InvalidStateTransition
from agent_engine.approvals.models import (
    RunRecord,
    RunStatus,
)
from agent_engine.runs.repository import RunRepository

DEFAULT_TERMINAL_RUN_TTL_SECONDS = 86_400.0  # 24 hours

_EXPIRATION_CLEANUP_BATCH_SIZE = 8

_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)


@dataclass(slots=True)
class _StoredRun:
    record: RunRecord
    expires_at: float | None = None
    token_legs: set[str] = field(default_factory=set)


class InMemoryRunRepository(RunRepository):
    """Process-local implementation of :class:`RunRepository`.

    This adapter assumes every call for an instance runs on one asyncio event
    loop and OS thread. Repository mutations contain no ``await`` points, so a
    coroutine cannot interleave with a check-and-mutate sequence. The adapter is
    not safe to share across threads or event loops and provides no cross-process
    guarantees. Terminal records are retained for ``terminal_ttl_seconds``;
    active and approval-pending records do not expire. An injected ``clock``
    must be monotonic so the bounded FIFO cleanup queue stays expiration-ordered.
    """

    def __init__(
        self,
        *,
        terminal_ttl_seconds: float = DEFAULT_TERMINAL_RUN_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not math.isfinite(terminal_ttl_seconds) or terminal_ttl_seconds <= 0:
            raise ValueError("terminal_ttl_seconds must be a positive finite number")
        self._runs: dict[str, _StoredRun] = {}
        self._expiration_queue: deque[tuple[float, str]] = deque()
        self._terminal_ttl_seconds = terminal_ttl_seconds
        self._clock = clock or time.monotonic

    async def create_if_absent(self, record: RunRecord) -> bool:
        """Register ``record`` unless its ``run_id`` is already known.

        The check and insert contain no suspension point, so exactly one caller
        on the owning event loop gets ``created=True``. A future shared adapter
        provides the same contract with its own atomic primitive.
        """
        run_id = record.run_id
        if run_id in self._runs:
            return False

        expires_at = (
            self._clock() + self._terminal_ttl_seconds
            if record.status in _TERMINAL_RUN_STATUSES
            else None
        )
        entry = _StoredRun(record=record, expires_at=expires_at)
        self._runs[run_id] = entry
        if expires_at is not None:
            self._expiration_queue.append((expires_at, run_id))
        return True

    async def get(self, run_id: str) -> RunRecord | None:
        entry = self._get_unexpired_entry(run_id)
        return None if entry is None else entry.record

    async def get_many(self, run_ids: Collection[str]) -> dict[str, RunRecord]:
        """Resolve many ids against the same dict, skipping unknown and expired ones."""
        found: dict[str, RunRecord] = {}
        for run_id in run_ids:
            entry = self._get_unexpired_entry(run_id)
            if entry is not None:
                found[run_id] = entry.record
        return found

    async def transition_if_allowed(self, run_id: str, target: RunStatus) -> bool:
        now = self._clock()
        entry = self._get_unexpired_entry(run_id, now)
        changed = False
        if entry is not None:
            try:
                entry.record.transition(target)
            except InvalidStateTransition:
                pass
            else:
                changed = True
                if target in _TERMINAL_RUN_STATUSES:
                    entry.expires_at = now + self._terminal_ttl_seconds
                    self._expiration_queue.append((entry.expires_at, run_id))
        self._evict_expired_batch(now)
        return changed

    async def add_token_usage(
        self,
        run_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        """Accumulate reported usage without introducing a suspension point."""
        entry = self._get_unexpired_entry(run_id)
        if entry is None:
            return None
        record = entry.record
        if input_tokens is not None:
            record.input_tokens = (record.input_tokens or 0) + input_tokens
        if output_tokens is not None:
            record.output_tokens = (record.output_tokens or 0) + output_tokens
        return record

    async def add_leg_token_usage(
        self,
        run_id: str,
        *,
        leg_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        """Record the leg key and its usage together, with no suspension point."""
        entry = self._get_unexpired_entry(run_id)
        if entry is None:
            return None
        if leg_id not in entry.token_legs:
            entry.token_legs.add(leg_id)
            await self.add_token_usage(
                run_id, input_tokens=input_tokens, output_tokens=output_tokens
            )
        return entry.record

    def _get_unexpired_entry(self, run_id: str, now: float | None = None) -> _StoredRun | None:
        entry = self._runs.get(run_id)
        if (
            entry is not None
            and entry.expires_at is not None
            and entry.expires_at <= (self._clock() if now is None else now)
            and entry.record.status in _TERMINAL_RUN_STATUSES
        ):
            self._runs.pop(run_id)
            return None
        return entry

    def _evict_expired_batch(self, now: float) -> None:
        for _ in range(_EXPIRATION_CLEANUP_BATCH_SIZE):
            if not self._expiration_queue or self._expiration_queue[0][0] > now:
                return
            expires_at, run_id = self._expiration_queue.popleft()
            current_entry = self._runs.get(run_id)
            if (
                current_entry is not None
                and current_entry.expires_at == expires_at
                and current_entry.record.status in _TERMINAL_RUN_STATUSES
            ):
                self._runs.pop(run_id)
