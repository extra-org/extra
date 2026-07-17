"""build_prompt windowing — a pure function, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_engine.engine.types import ChatMessage, ChatRole
from agent_manager.application.context import build_history, build_prompt
from agent_manager.domain import Message, Role


def _msg(role: Role, content: str) -> Message:
    return Message(role=role, content=content, created_at=datetime.now(UTC))


def test_no_history_returns_message_unchanged() -> None:
    assert build_prompt([], "turn it off") == "turn it off"


def test_history_is_inlined_in_order_then_new_message() -> None:
    history = [_msg(Role.USER, "turn on kitchen lights"), _msg(Role.ASSISTANT, "done")]
    out = build_prompt(history, "turn it off")
    assert "user: turn on kitchen lights" in out
    assert "assistant: done" in out
    assert out.index("turn on kitchen lights") < out.index("turn it off")
    assert out.rstrip().endswith("turn it off")


def test_window_caps_to_most_recent_messages() -> None:
    history = [_msg(Role.USER, f"m{i}") for i in range(20)]
    out = build_prompt(history, "new", window=3)
    assert "m19" in out and "m17" in out
    assert "m16" not in out


def test_structured_history_preserves_roles_and_order() -> None:
    history = [_msg(Role.USER, "first"), _msg(Role.ASSISTANT, "choice one")]

    assert build_history(history) == (
        ChatMessage(ChatRole.USER, "first"),
        ChatMessage(ChatRole.ASSISTANT, "choice one"),
    )
