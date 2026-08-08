from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from agent_engine.approvals.manager import ApprovalManager
from agent_engine.approvals.models import RunRecord, RunStatus, can_run_transition
from agent_engine.engine.types import RunResult
from agent_engine.logging_config import log
from agent_engine.runtime.hooks import HookManager, RunContext, RunEndContext

logger = logging.getLogger(__name__)


def _noop() -> None:
    """Default ``on_completed`` callback: publish nothing between the phases."""


class RunLifecycle:
    """Coordinates run registration, lifecycle hooks, and lifecycle logging."""

    def __init__(
        self,
        *,
        system_name: str,
        hook_manager: HookManager,
        approval_manager: ApprovalManager,
    ) -> None:
        self._system_name = system_name
        self._hooks = hook_manager
        self._runs = approval_manager

    # -- start ---------------------------------------------------------------

    @staticmethod
    def identify(context: RunContext | None) -> RunContext:
        """Return the caller's context with a run id guaranteed."""
        ctx = context or RunContext()
        if ctx.run_id is None:
            ctx = ctx.replace(run_id=str(uuid.uuid4()))
        return ctx

    async def begin(self, context: RunContext | None) -> RunContext:
        """Open a run. ``on_run_start`` may replace the context, so it runs
        before registration."""
        ctx = await self._hooks.run_run_start(self.identify(context))
        await self._register(ctx)
        log(logger, logging.INFO, "run started", run_id=ctx.run_id, system=self._system_name)
        return ctx

    async def _register(self, ctx: RunContext) -> None:
        """Record the run as ``RUNNING``. ``register_run`` is itself
        create-if-absent, so a concurrent caller for the same ``run_id`` cannot
        clobber a record another caller already wrote."""
        assert ctx.run_id is not None
        await self._runs.register_run(
            RunRecord(
                run_id=ctx.run_id,
                thread_id=ctx.run_id,
                system_name=self._system_name,
                status=RunStatus.RUNNING,
            )
        )

    async def succeed(
        self,
        ctx: RunContext,
        result: RunResult,
        *,
        on_completed: Callable[[], None] = _noop,
    ) -> None:
        """Close a run that produced an answer. ``on_completed`` fires between
        the status change and ``on_run_end``; callers depend on that order."""
        await self._mark(ctx.run_id, RunStatus.COMPLETED)
        on_completed()
        await self._hooks.run_run_end(ctx, self._end_context(ctx, result))
        log(
            logger,
            logging.INFO,
            "run ended",
            run_id=ctx.run_id,
            system=self._system_name,
            visited=len(result.visited),
            tools=len(result.used_tools),
        )

    async def fail(self, ctx: RunContext, error: Exception) -> None:
        """Close a run that raised. Never raises, so the original error survives."""
        log(
            logger,
            logging.WARNING,
            "run failed",
            run_id=ctx.run_id,
            system=self._system_name,
            error=type(error).__name__,
        )
        await self._mark(ctx.run_id, RunStatus.FAILED)
        await self._hooks.run_run_error(ctx, error)

    async def cancel(self, ctx: RunContext) -> None:
        """Close an in-flight run whose streaming consumer went away.

        A suspended run remains pending because its checkpoint is durable and
        can still be resumed from another connection. Completed and failed runs
        are terminal already, so cancellation leaves them unchanged too.
        """
        if await self._mark(ctx.run_id, RunStatus.CANCELLED):
            log(
                logger,
                logging.INFO,
                "run cancelled",
                run_id=ctx.run_id,
                system=self._system_name,
                reason="consumer abandoned stream",
            )

    async def _mark(self, run_id: str | None, target: RunStatus) -> bool:
        """Move a run to ``target`` if the state machine allows it, else leave it."""
        run = await self._registered(run_id)
        if run is not None and can_run_transition(run.status, target):
            await self._runs.mark_run(run.run_id, target)
            return True
        return False

    async def _registered(self, run_id: str | None) -> RunRecord | None:
        """The registry's record for a run, if it has one."""
        return None if run_id is None else await self._runs.get_run_or_none(run_id)

    def _end_context(self, ctx: RunContext, result: RunResult) -> RunEndContext:
        """Safe summary of a completed run for ``on_run_end`` (no answer text)."""
        return RunEndContext(
            run_id=ctx.run_id,
            system_name=self._system_name,
            status="succeeded",
            visited=tuple(result.visited),
            used_tool_count=len(result.used_tools),
        )
