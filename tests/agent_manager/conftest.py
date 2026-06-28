"""Shared test doubles for the agent_manager layers."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_engine.engine.engine import Engine
from agent_engine.engine.types import RunResult
from agent_engine.runtime.hooks.models import RunContext
from agent_engine.runtime.streaming import RunStreamEvent


class RecordingEngine(Engine):
    """A stub Engine that records prompts and echoes a canned answer."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.contexts: list[RunContext | None] = []

    async def build(self, _spec: object) -> None: ...

    async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
        self.prompts.append(message)
        self.contexts.append(context)
        return RunResult(system_name="stub", visited=["agent"], answer=f"answer:{message[-20:]}")

    async def stream(
        self, message: str, *, context: RunContext | None = None
    ) -> AsyncIterator[RunStreamEvent]:
        self.prompts.append(message)
        self.contexts.append(context)
        yield RunStreamEvent(type="answer_delta", content="x")
