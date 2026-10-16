"""Exclusive supervisor lifetime, separate from durable per-event execution claims."""

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager


class ChannelWorkerAlreadyRunning(RuntimeError):
    """Another supervisor owns the configured connection."""


class ChannelWorkerOwnershipLost(RuntimeError):
    """Claims and child work must stop because exclusive ownership is uncertain."""


class ChannelWorkerOwnership(ABC):
    @abstractmethod
    def hold(self) -> AbstractAsyncContextManager[None]: ...

    @abstractmethod
    async def check(self) -> None:
        """Raise rather than silently reconnect/reacquire a lost ownership session."""
