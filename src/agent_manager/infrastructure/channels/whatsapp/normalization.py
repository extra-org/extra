"""Pure normalization of a verified batch; invalid siblings are quarantined."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from agent_manager.domain.channels.models import (
    ChannelAddress,
    InboundMessage,
    MessageKind,
    bounded_identifier,
)
from agent_manager.domain.channels.webhooks import DeliveryEvent, WebhookBatch, WebhookIssue
from agent_manager.infrastructure.channels.whatsapp.config import WhatsAppWebhookConfig
from agent_manager.infrastructure.channels.whatsapp.payloads import (
    Change,
    Entry,
    Envelope,
    Message,
    Messages,
    RoutingValue,
    Status,
    Statuses,
)


def normalize(body: bytes, config: WhatsAppWebhookConfig, received_at: datetime) -> WebhookBatch:
    messages: list[InboundMessage] = []
    delivery: list[DeliveryEvent] = []
    issues: list[WebhookIssue] = []
    try:
        envelope = Envelope.model_validate_json(body)
    except (ValidationError, ValueError):
        return WebhookBatch(issues=(WebhookIssue("body", "invalid_envelope"),))
    if envelope.object != "whatsapp_business_account":
        return WebhookBatch()
    if "entry" not in envelope.model_fields_set:
        return WebhookBatch(issues=(WebhookIssue("body", "invalid_envelope"),))
    for entry_index, raw_entry in enumerate(envelope.entry):
        path = f"entry.{entry_index}"
        # Other accounts must not affect this connection, even when malformed.
        if (
            isinstance(raw_entry, dict)
            and "id" in raw_entry
            and raw_entry["id"] != config.account_id
        ):
            continue
        try:
            entry = Entry.model_validate(raw_entry)
        except ValidationError:
            issues.append(WebhookIssue(path, "invalid_entry"))
            continue
        for change_index, raw_change in enumerate(entry.changes):
            location = f"{path}.changes.{change_index}"
            try:
                change = Change.model_validate(raw_change)
            except ValidationError:
                issues.append(WebhookIssue(location, "invalid_change"))
                continue
            if change.field != "messages":
                continue
            try:
                routing = RoutingValue.model_validate(change.value)
                if routing.metadata.phone_number_id != config.phone_number_id:
                    continue
            except ValidationError:
                issues.append(WebhookIssue(location, "invalid_value"))
                continue
            try:
                inbound = Messages.model_validate(change.value).messages
            except ValidationError:
                issues.append(WebhookIssue(f"{location}.messages", "invalid_value"))
                inbound = []
            try:
                statuses = Statuses.model_validate(change.value).statuses
            except ValidationError:
                issues.append(WebhookIssue(f"{location}.statuses", "invalid_value"))
                statuses = []
            for index, raw in enumerate(inbound):
                item_path = f"{location}.messages.{index}"
                try:
                    message = Message.model_validate(raw)
                    if message.type not in {
                        "text",
                        "image",
                        "audio",
                        "video",
                        "document",
                        "sticker",
                        "location",
                        "contacts",
                        "interactive",
                        "button",
                        "order",
                    }:
                        raise ValueError("not a supported customer message kind")
                    sent_at = _timestamp(message.timestamp, received_at)
                    if message.type == "text" and message.text is None:
                        raise ValueError("missing text")
                    messages.append(
                        InboundMessage(
                            ChannelAddress(config.connection_id, config.system_id, message.sender),
                            message.id,
                            message.text.body if message.type == "text" and message.text else "",
                            received_at,
                            sent_at,
                            _kind(message.text.body)
                            if message.type == "text" and message.text
                            else "unsupported",
                        )
                    )
                except (ValidationError, ValueError, OverflowError, OSError):
                    issues.append(WebhookIssue(item_path, "invalid_message"))
            for index, raw in enumerate(statuses):
                item_path = f"{location}.statuses.{index}"
                try:
                    status = Status.model_validate(raw)
                    for identifier in (status.id, status.recipient_id):
                        bounded_identifier(identifier)
                    bounded_identifier(status.status, limit=64)
                    delivery.append(
                        DeliveryEvent(
                            status.id,
                            status.recipient_id,
                            status.status,
                            _timestamp(status.timestamp, received_at),
                        )
                    )
                except (ValidationError, ValueError, OverflowError, OSError):
                    issues.append(WebhookIssue(item_path, "invalid_status"))
    if len(issues) > 256:
        issues = [*issues[:255], WebhookIssue("body", "issue_limit")]
    return WebhookBatch(tuple(messages), tuple(delivery), tuple(issues))


def _kind(body: str) -> MessageKind:
    commands: dict[str, MessageKind] = {
        "/new": "reset",
        "שיחה חדשה": "reset",
        "/help": "help",
        "נציג": "help",
        "/stop": "stop",
        "הפסק": "stop",
        "/start": "start",
        "המשך": "start",
    }
    return commands.get(body.strip().casefold(), "text")


def _timestamp(value: str, received_at: datetime) -> datetime:
    timestamp = datetime.fromtimestamp(int(value), tz=UTC)
    if timestamp > received_at + timedelta(minutes=5):
        raise ValueError("future provider timestamp")
    return timestamp
