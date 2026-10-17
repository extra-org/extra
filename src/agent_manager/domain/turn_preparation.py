"""Optional atomic preparation capability, independent of any message provider."""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from agent_manager.domain.models import ConversationMessage, Role


class TurnPreparationConflict(ValueError):
    """An idempotency key already belongs to different preparation input."""


def preparation_key_digest(key: str) -> str:
    if not key.strip() or len(key.encode("utf-8")) > 512:
        raise ValueError("Idempotency key must contain 1 to 512 UTF-8 bytes")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def validate_prepared_message(
    message: ConversationMessage, conversation_id: str, user_id: str, content: str
) -> None:
    if (
        message.session_id != conversation_id
        or message.user_id != user_id
        or message.content != content
        or message.role is not Role.USER
        or message.run_id is None
    ):
        raise TurnPreparationConflict("Idempotency key is bound to different turn input")


@runtime_checkable
class IdempotentTurnRepository(Protocol):
    """A receipt and its user append share one atomic commit.

    An existing receipt returns the original message regardless of the current
    branch head, after validating owner/conversation/content. A new key whose
    expected head is stale returns None and writes nothing. Implementations
    must not use a separate receipt transaction after appending the message.
    """

    async def get_prepared_message(self, key_digest: str) -> ConversationMessage | None: ...

    async def append_prepared_message(
        self,
        key_digest: str,
        message: ConversationMessage,
        expected_head_message_id: str | None,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> ConversationMessage | None: ...
