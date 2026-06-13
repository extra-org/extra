from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from agent_engine.core.spec import SystemSpec
from agent_engine.engine.types import Message, RunResult
from agent_engine.runtime.streaming import RunStreamEvent


class Engine(ABC):
    @abstractmethod
    def build(self, spec: SystemSpec) -> None: ...

    @abstractmethod
    def run(self, message: str) -> RunResult: ...

    @abstractmethod
    def stream(self, message: str) -> Iterator[RunStreamEvent]: ...

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
