"""The conversation-titling port. Adapters implement it; the application calls it."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TitleGenerator(ABC):
    """Names a conversation from its opening message."""

    @abstractmethod
    async def generate(self, text: str, conversation_id: str) -> str:
        """A display-ready title for the conversation opening with `text`.

        Never raises for a failed naming: an implementation degrades to
        something reasonable instead, because a title is cosmetic and the turn
        it describes is not. `conversation_id` identifies the work in traces
        and logs; it is not a lookup key — the message is the whole input.
        """
