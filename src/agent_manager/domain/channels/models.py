"""Bounded normalized messages and durable work state, never provider payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

MessageKind = Literal["text", "reset", "unsupported", "help", "stop", "start"]


class ChannelMessageConflict(ValueError):
    """A stable message or reply key was reused for different input."""


class ChannelClaimConflict(RuntimeError):
    """The caller does not own this work in the required state."""


class ChannelOwnerConflict(ValueError):
    """A sender binding cannot implicitly change organization, user, or chat."""


def bounded_identifier(value: str, *, limit: int = 512) -> None:
    if "\x00" in value:
        raise ValueError("channel identifiers must not contain NUL")
    if not value.strip() or len(value.encode("utf-8")) > limit:
        raise ValueError(f"channel identifier must contain 1 to {limit} UTF-8 bytes")


def bounded_text(value: str) -> None:
    if "\x00" in value:
        raise ValueError("channel text must not contain NUL")
    if len(value.encode("utf-8")) > 65_536:
        raise ValueError("normalized channel text exceeds 65536 UTF-8 bytes")


def stable_id(*parts: str) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ChannelAddress:
    connection_id: str
    system_id: str
    sender_id: str = field(repr=False)

    def __post_init__(self) -> None:
        bounded_identifier(self.connection_id, limit=128)
        bounded_identifier(self.system_id, limit=256)
        bounded_identifier(self.sender_id)


@dataclass(frozen=True)
class InboundMessage:
    address: ChannelAddress
    provider_message_id: str
    content: str = field(repr=False)
    received_at: datetime
    sent_at: datetime | None
    kind: MessageKind = "text"

    def __post_init__(self) -> None:
        bounded_identifier(self.provider_message_id)
        bounded_text(self.content)
        if self.kind not in {"text", "reset", "unsupported", "help", "stop", "start"}:
            raise ValueError("unsupported normalized channel message kind")
        if self.kind in {"text", "reset"} and not self.content.strip():
            raise ValueError("channel text must not be empty")
        for name in ("received_at", "sent_at"):
            value = getattr(self, name)
            if value is not None:
                if value.utcoffset() is None:
                    raise ValueError("channel timestamps must include a timezone")
                object.__setattr__(self, name, value.astimezone(UTC))


class InboxState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    PREPARED = "prepared"
    RUNNING = "running"
    ANSWERED = "answered"
    DONE = "done"
    DENIED = "denied"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class InboxItem:
    event_id: str
    sequence: int
    message: InboundMessage
    state: InboxState
    claim_token: str | None = field(default=None, repr=False)
    conversation_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class ChannelBinding:
    address: ChannelAddress
    generation: int
    user_id: str | None = None
    organization_id: str | None = None
    conversation_id: str | None = None
    active_event_id: str | None = None


@dataclass(frozen=True)
class StagedReply:
    reply_id: str
    event_id: str
    response_id: str
    part_index: int
    text: str = field(repr=False)
    created_at: datetime
