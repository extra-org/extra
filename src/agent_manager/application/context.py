"""Convert persisted conversation turns to the engine's structured history."""

from __future__ import annotations

from agent_engine.engine.types import ChatMessage, ChatRole
from agent_manager.domain import Message, Role


def build_history(history: list[Message], window: int = 10) -> tuple[ChatMessage, ...]:
    """Return recent user/assistant turns in provider-independent structured form."""
    recent = history[-window:] if window else history
    roles = {
        Role.USER: ChatRole.USER,
        Role.ASSISTANT: ChatRole.ASSISTANT,
    }
    return tuple(
        ChatMessage(role=roles[message.role], content=message.content)
        for message in recent
        if message.role in roles
    )


def build_prompt(history: list[Message], new_message: str, window: int = 10) -> str:
    """Build the legacy flattened transcript used by older standalone examples.

    ConversationService no longer uses this compatibility helper; it passes
    :func:`build_history` through the engine's structured history boundary.
    """
    recent = history[-window:] if window else history
    if not recent:
        return new_message
    transcript = "\n".join(f"{m.role}: {m.content}" for m in recent)
    return (
        "Conversation so far:\n"
        f"{transcript}\n\n"
        "Now respond to the latest user message:\n"
        f"{new_message}"
    )
