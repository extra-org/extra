from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from agent_engine.core.spec import SystemSpec
from agent_engine.engine.types import RunResult
from agent_engine.runtime.streaming import RunStreamEvent


class Engine(ABC):
    @abstractmethod
    async def build(self, spec: SystemSpec) -> None: ...

    @abstractmethod
    async def run(self, message: str) -> RunResult: ...

    @abstractmethod
    async def stream(self, message: str) -> AsyncIterator[RunStreamEvent]: ...

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> Engine:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
