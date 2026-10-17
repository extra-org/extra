"""Normalized authenticated batch receipts; provider payloads stay at the edge."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from agent_manager.domain.channels.models import InboundMessage

IssueReason = Literal[
    "invalid_envelope",
    "invalid_entry",
    "invalid_change",
    "invalid_value",
    "invalid_message",
    "invalid_status",
    "conflicting_message",
    "issue_limit",
]


@dataclass(frozen=True)
class WebhookIssue:
    location: str
    reason: IssueReason


@dataclass(frozen=True)
class DeliveryEvent:
    provider_message_id: str
    recipient_id: str = field(repr=False)
    status: str
    observed_at: datetime


@dataclass(frozen=True)
class WebhookBatch:
    messages: tuple[InboundMessage, ...] = ()
    delivery_events: tuple[DeliveryEvent, ...] = ()
    issues: tuple[WebhookIssue, ...] = ()


@dataclass(frozen=True)
class WebhookReceipt:
    receipt_id: str
    connection_id: str
    system_id: str
    body_digest: str
    received_at: datetime
    event_ids: tuple[str, ...]
    delivery_event_ids: tuple[str, ...]
    issues: tuple[WebhookIssue, ...]


class WebhookRepository(ABC):
    @abstractmethod
    async def accept(
        self,
        connection_id: str,
        system_id: str,
        body_digest: str,
        received_at: datetime,
        batch: WebhookBatch,
    ) -> WebhookReceipt:
        """Commit the complete verified batch atomically or leave it unrecorded."""

    @abstractmethod
    async def get_receipt(self, receipt_id: str) -> WebhookReceipt | None: ...
