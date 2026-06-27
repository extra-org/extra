"""Build the per-request prompt by inlining recent history.

# ponytail: string-prepended transcript. Upgrade path when richer context is
# needed: pass history via RunContext.metadata + a declared resolver (Engine.run
# already accepts a `context` arg). Not needed to prove the layer works.
"""

from __future__ import annotations

from agent_manager.domain import Message


def build_prompt(history: list[Message], new_message: str, window: int = 10) -> str:
    """Prepend up to `window` most-recent prior messages to `new_message`.

    `history` is the prior messages (oldest-first), NOT including the new one.
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
