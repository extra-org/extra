"""Malformed input isolation and conservative provider-contract normalization."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from agent_manager.infrastructure.channels.whatsapp.config import WhatsAppWebhookConfig
from agent_manager.infrastructure.channels.whatsapp.normalization import normalize
from tests.agent_manager.test_whatsapp_webhook import change, encoded, message, payload

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
CONFIG = WhatsAppWebhookConfig(
    "connection", "system", "account-1", "number-1", SecretStr("test-app"), SecretStr("test-verify")
)


def inbound(**overrides):
    return message(timestamp=str(int(NOW.timestamp())), **overrides)


def test_missing_entry_or_missing_entry_identity_is_quarantined():
    for data in (
        {"object": "whatsapp_business_account"},
        {"object": "whatsapp_business_account", "entry": [{"changes": []}]},
    ):
        batch = normalize(encoded(data), CONFIG, NOW)
        assert batch.issues and not batch.messages


def test_invalid_message_collection_keeps_valid_delivery_events():
    data = payload(
        change(
            statuses=[
                {
                    "id": "outbound",
                    "recipient_id": "sender",
                    "status": "read",
                    "timestamp": str(int(NOW.timestamp())),
                }
            ]
        )
    )
    data["entry"][0]["changes"][0]["value"]["messages"] = "invalid collection"
    batch = normalize(encoded(data), CONFIG, NOW)
    assert len(batch.delivery_events) == 1
    assert batch.issues and not batch.messages


def test_invalid_status_collection_keeps_valid_messages():
    data = payload(change(inbound()))
    data["entry"][0]["changes"][0]["value"]["statuses"] = {}
    batch = normalize(encoded(data), CONFIG, NOW)
    assert len(batch.messages) == 1
    assert batch.issues and not batch.delivery_events


@pytest.mark.parametrize(
    "changed",
    [
        {"from": ""},
        {"from": " "},
        {"from": "private\x00sender"},
        {"from": "🙂" * 129},
        {"id": 123},
        {"id": ""},
        {"id": "private\x00message"},
        {"text": None},
        {"text": {"body": "   "}},
        {"text": {"body": "private\x00text"}},
        {"text": {"body": 123}},
        {"text": {"body": "🙂" * 20_000}},
        {"timestamp": "invalid"},
        {"timestamp": "99999999999999999999"},
        {"timestamp": str(int(NOW.timestamp()) + 3600)},
    ],
)
def test_invalid_message_is_quarantined_without_private_input(changed):
    data = payload(change({**inbound(), **changed}, inbound(id="second")))
    batch = normalize(encoded(data), CONFIG, NOW)
    assert [item.provider_message_id for item in batch.messages] == ["second"]
    assert [(issue.location, issue.reason) for issue in batch.issues] == [
        ("entry.0.changes.0.messages.0", "invalid_message")
    ]


def test_unsupported_media_is_not_downloaded_or_promoted_to_agent_text():
    data = payload(
        change(
            inbound(
                type="image",
                image={"link": "https://private.invalid/image", "caption": "private caption"},
                text=None,
            )
        )
    )
    batch = normalize(encoded(data), CONFIG, NOW)
    assert len(batch.messages) == 1
    assert batch.messages[0].kind == "unsupported" and batch.messages[0].content == ""
    assert not batch.issues


def test_unverified_new_sender_contract_is_quarantined_instead_of_guessed():
    data = inbound(from_user_id="new-business-scoped-id")
    del data["from"]
    batch = normalize(encoded(payload(change(data))), CONFIG, NOW)
    assert not batch.messages and batch.issues[0].reason == "invalid_message"


def test_quarantine_metadata_is_bounded_for_many_bad_messages():
    batch = normalize(encoded(payload(change(*([None] * 300)))), CONFIG, NOW)
    assert len(batch.issues) == 256
    assert batch.issues[-1].reason == "issue_limit"


def test_unpersistable_startup_system_id_is_rejected_before_accepting_callbacks():
    with pytest.raises(ValueError):
        replace(CONFIG, system_id="x" * 257)


@pytest.mark.parametrize(
    "body,kind",
    [
        (" /NEW ", "reset"),
        ("שיחה חדשה", "reset"),
        ("נציג", "help"),
        ("/stop", "stop"),
        ("הפסק", "stop"),
        ("/start", "start"),
        ("המשך", "start"),
        ("/help find a file", "text"),
    ],
)
def test_only_exact_control_commands_bypass_agent_text(body, kind):
    batch = normalize(encoded(payload(change(inbound(text={"body": body})))), CONFIG, NOW)
    assert batch.messages[0].kind == kind
    assert batch.messages[0].content == body


@pytest.mark.parametrize("kind", ["system", "unknown", "unverified-provider-kind"])
def test_provider_system_notifications_do_not_open_a_customer_service_window(kind):
    batch = normalize(encoded(payload(change(inbound(type=kind)))), CONFIG, NOW)
    assert batch.messages == ()
    assert batch.issues[0].reason == "invalid_message"


@pytest.mark.parametrize("field", ["id", "recipient_id", "status"])
def test_unpersistable_status_keeps_valid_messages_and_status_siblings(field):
    status = {
        "id": "outbound",
        "recipient_id": "sender",
        "status": "delivered",
        "timestamp": str(int(NOW.timestamp())),
    }
    data = payload(change(inbound(), statuses=[{**status, field: "private\x00value"}, status]))
    batch = normalize(encoded(data), CONFIG, NOW)
    assert [item.provider_message_id for item in batch.messages] == ["wamid.one"]
    assert len(batch.delivery_events) == 1
    assert batch.delivery_events[0].provider_message_id == "outbound"
    assert [(issue.location, issue.reason) for issue in batch.issues] == [
        ("entry.0.changes.0.statuses.0", "invalid_status")
    ]
