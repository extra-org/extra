"""One supervised connection owner with bounded independent-sender jobs."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import AsyncExitStack
from types import TracebackType

from agent_manager.application.channels.delivery import ChannelDeliveryService
from agent_manager.application.channels.processing import ChannelTurnProcessor
from agent_manager.application.channels.worker_options import ChannelWorkerOptions
from agent_manager.domain.channels.models import InboxItem
from agent_manager.domain.channels.ownership import (
    ChannelWorkerOwnership,
    ChannelWorkerOwnershipLost,
)
from agent_manager.domain.channels.repository import ChannelRepository

logger = logging.getLogger(__name__)


class ChannelWorker:
    def __init__(
        self,
        repository: ChannelRepository,
        processor: ChannelTurnProcessor,
        ownership: ChannelWorkerOwnership,
        options: ChannelWorkerOptions,
        *,
        delivery: ChannelDeliveryService | None = None,
    ) -> None:
        self._repository = repository
        self._processor = processor
        self._ownership = ownership
        self._options = options
        self._delivery = delivery
        self._stack = AsyncExitStack()
        self._stop = asyncio.Event()
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._pending: deque[InboxItem] = deque()
        self._cursor = 0
        self._driver: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._driver is not None and not self._driver.done() and not self._stop.is_set()

    async def __aenter__(self) -> ChannelWorker:
        if self._driver is not None:
            raise RuntimeError("channel worker cannot be started twice")
        try:
            await self._stack.enter_async_context(self._ownership.hold())
            await self._ownership.check()
            self._driver = asyncio.create_task(self._serve())
            self._driver.add_done_callback(self._observe_failure)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        shutdown = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(shutdown)
        except asyncio.CancelledError:
            # The lifespan caller can itself be cancelled during shutdown.
            # Keep the complete drain/ownership scope alive, even through
            # repeated cancellation, then propagate the caller's cancellation.
            while not shutdown.done():
                try:
                    await asyncio.shield(shutdown)
                except asyncio.CancelledError:
                    continue
            shutdown.result()
            raise

    async def _shutdown(self) -> None:
        try:
            if self._driver is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._driver), self._options.drain_seconds + 1
                    )
                except TimeoutError:
                    self._driver.cancel()
                    await asyncio.gather(self._driver, return_exceptions=True)
        finally:
            await self._stack.aclose()

    async def _serve(self) -> None:
        failed = False
        try:
            while not self._stop.is_set():
                await self._ownership.check()
                self._reap()
                try:
                    await self._schedule()
                    if self._delivery is not None and not self._stop.is_set():
                        await self._ownership.check()
                        await self._delivery.tick(self._stop)
                except ChannelWorkerOwnershipLost:
                    raise
                except Exception as exc:
                    logger.warning("channel scheduling failed: type=%s", type(exc).__name__)
                stop_waiter = asyncio.create_task(self._stop.wait())
                try:
                    await asyncio.wait(
                        (stop_waiter, *self._jobs.values()),
                        timeout=self._options.poll_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    stop_waiter.cancel()
                    await asyncio.gather(stop_waiter, return_exceptions=True)
        except BaseException:
            self._stop.set()
            failed = True
            raise
        finally:
            cleanup = asyncio.create_task(self._drain(immediate=failed))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # A supervisor timeout must not interrupt the engine's own
                # cancellation cleanup and release ownership ahead of it.
                await cleanup
                raise

    async def _schedule(self) -> None:
        scanned = False
        while not self._stop.is_set() and len(self._jobs) < self._options.concurrency:
            if self._pending:
                item = self._pending.popleft()
            elif not scanned:
                page = await self._repository.list_active(
                    self._options.connection_id,
                    self._options.system_id,
                    after_sequence=self._cursor,
                )
                self._cursor = page[-1].sequence if page else 0
                self._pending.extend(page)
                scanned = True
                continue
            else:
                claimed = await self._repository.claim_next(
                    self._options.connection_id, self._options.system_id
                )
                if claimed is None:
                    return
                item = claimed
            if self._stop.is_set():
                # An in-flight SQL claim may commit after shutdown started.
                # Keep it preparing for the next owner without starting work.
                return
            if item.event_id not in self._jobs:
                self._jobs[item.event_id] = asyncio.create_task(self._processor.process(item))

    def _reap(self) -> None:
        for event_id, task in tuple(self._jobs.items()):
            if task.done():
                if not task.cancelled() and task.exception() is not None:
                    logger.warning(
                        "channel task failed: event=%s type=%s",
                        event_id,
                        type(task.exception()).__name__,
                    )
                del self._jobs[event_id]

    async def _drain(self, *, immediate: bool) -> None:
        tasks = tuple(self._jobs.values())
        if tasks:
            _, pending = await asyncio.wait(
                tasks, timeout=0 if immediate else self._options.drain_seconds
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()

    @staticmethod
    def _observe_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("channel supervisor stopped: type=%s", type(task.exception()).__name__)
