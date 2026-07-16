from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agent_manager.application.context import build_prompt
from agent_manager.domain import Message, Role


@dataclass
class InMemoryHistory:
    window: int = 12
    _messages: dict[str, list[Message]] = field(default_factory=dict)
    _tool_events: dict[str, list[str]] = field(default_factory=dict)

    def prompt(self, session_id: str, message: str) -> str:
        return build_prompt(self._messages.get(session_id, []), message, self.window)

    def append(self, session_id: str, role: Role, content: str) -> None:
        self._messages.setdefault(session_id, []).append(
            Message(role=role, content=content, created_at=datetime.now(UTC))
        )

    def list(self, session_id: str) -> tuple[Message, ...]:
        return tuple(self._messages.get(session_id, []))

    def clear(self, session_id: str) -> None:
        self._messages.pop(session_id, None)
        self._tool_events.pop(session_id, None)

    def record_tools(self, session_id: str, tools: Iterable[str]) -> None:
        self._tool_events.setdefault(session_id, []).extend(tools)

    def tool_events(self, session_id: str) -> tuple[str, ...]:
        return tuple(self._tool_events.get(session_id, []))
