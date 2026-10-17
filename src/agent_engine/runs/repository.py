"""Abstract persistence contract for run lifecycle state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection

from agent_engine.approvals.models import RunRecord, RunStatus


class RunRepository(ABC):
    """Persistence contract for run records.

    ``create_if_absent`` is the atomic semantic boundary: implementations must
    store ``record`` only when its ``run_id`` is unknown, must never overwrite
    an existing record, and must report whether this call created it. Atomicity
    applies to the implementation's consistency scope.
    The in-memory adapter is process-local; a future shared adapter must provide
    the same semantics across processes.
    """

    @abstractmethod
    async def create_if_absent(self, record: RunRecord) -> bool:
        """Return ``True`` if created, or ``False`` without replacing existing state.

        An existing ``run_id`` is an idempotent outcome, not an error. Backend
        failures may still propagate from an implementation.
        """
        raise NotImplementedError

    @abstractmethod
    async def get(self, run_id: str) -> RunRecord | None:
        """Return the stored run, or ``None`` when ``run_id`` is unknown."""
        raise NotImplementedError

    @abstractmethod
    async def get_many(self, run_ids: Collection[str]) -> dict[str, RunRecord]:
        """Return the stored runs for ``run_ids``, keyed by id.

        Unknown ids are simply absent. Implementations must answer in a bounded
        number of round trips so callers projecting many runs at once — history
        endpoints in particular — do not degrade into one query per run.
        """
        raise NotImplementedError

    @abstractmethod
    async def transition_if_allowed(self, run_id: str, target: RunStatus) -> bool:
        """Atomically apply an allowed transition.

        Return ``True`` only when this call changed the stored status. Return
        ``False`` when the run is absent or the transition is not allowed,
        without modifying stored state.
        """
        raise NotImplementedError

    @abstractmethod
    async def add_token_usage(
        self,
        run_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        """Atomically add one execution leg's reported usage to a run.

        ``None`` means the provider did not report that value. It does not erase
        usage recorded by an earlier execution leg.
        """
        raise NotImplementedError

    async def add_leg_token_usage(
        self,
        run_id: str,
        *,
        leg_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        """Add one execution leg's usage once per ``leg_id``.

        Retrying a leg whose earlier write committed but failed to acknowledge
        must not charge it again, while different legs of one run, including
        overlapping ones, each apply. The default adds unconditionally; adapters
        override it to record the key atomically with the totals.
        """
        return await self.add_token_usage(
            run_id, input_tokens=input_tokens, output_tokens=output_tokens
        )
