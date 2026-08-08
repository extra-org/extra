from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from agent_engine.runtime.streaming import RunStreamEvent, StreamSinks


class StreamChannel:
    """An in-order channel of :class:`RunStreamEvent` for one streamed run."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[RunStreamEvent | BaseException | None] = asyncio.Queue()

    def sinks(self, *, token: Callable[[int, int], None]) -> StreamSinks:
        """Per-run sinks publishing onto this channel. Install before creating
        the graph task — a task copies the context vars as they are then."""
        return StreamSinks(
            answer=lambda content: self.emit(RunStreamEvent(type="answer_delta", content=content)),
            route=lambda route: self.emit(RunStreamEvent(type="route", route=route)),
            token=token,
        )

    def emit(self, event: RunStreamEvent) -> None:
        """Publish an event. Non-blocking, so it is safe from sync sink callbacks."""
        self._queue.put_nowait(event)

    def abort(self, error: BaseException) -> None:
        """Hand a producer failure to the consumer, after anything already emitted."""
        self._queue.put_nowait(error)

    def close(self) -> None:
        """Signal that no further items will be published (``None`` sentinel)."""
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[RunStreamEvent]:
        """Yield events until the producer closes the channel, re-raising a
        producer failure in the consumer's task."""
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise RuntimeError(str(item)) from item
            yield item
