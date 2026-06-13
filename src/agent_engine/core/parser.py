from __future__ import annotations

from abc import ABC, abstractmethod

from agent_engine.core.spec import SystemSpec


class Parser(ABC):
    @abstractmethod
    def parse(self, path: str) -> SystemSpec: ...
