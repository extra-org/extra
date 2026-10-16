"""Real manager HTTP composition and file SQL; no provider, model, or MCP calls."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import SQLModel, select

from agent_manager.api.app import create_app
from agent_manager.config import Settings
from agent_manager.infrastructure.persistence.channels.tables import ChannelInboxRow
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.tables import ConversationSessionRow
from tests.agent_manager.conftest import RecordingEngine

ENDPOINT = "/channels/whatsapp/webhook"
APP_SECRET = "test-only-whatsapp-app-secret"
VERIFY_TOKEN = "test-only-subscription-token"


def settings_values(**overrides):
    return {
        "extra_whatsapp_enabled": True,
        "extra_whatsapp_connection_id": "business-number",
        "extra_whatsapp_account_id": "account-1",
        "extra_whatsapp_phone_number_id": "number-1",
        "extra_whatsapp_app_secret": APP_SECRET,
        "extra_whatsapp_verify_token": VERIFY_TOKEN,
        **overrides,
    }


def message(provider_id="wamid.one", **overrides):
    return {
        "id": provider_id,
        "from": "opaque-sender",
        "timestamp": str(int(datetime.now(UTC).timestamp()) - 60),
        "type": "text",
        "text": {"body": "שלום 👋\nHow can I help?"},
        **overrides,
    }


def change(*messages, phone="number-1", statuses=None):
    return {
        "field": "messages",
        "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": phone, "display_phone_number": "not-an-identity"},
            "messages": list(messages),
            "statuses": statuses or [],
        },
    }


def payload(*changes, account="account-1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": account, "changes": list(changes)}],
    }


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode()


def signed(body: bytes) -> dict[str, str]:
    signature = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={signature}", "Content-Type": "application/json"}


class StartupEngine(RecordingEngine):
    def __init__(self):
        super().__init__()
        self.builds = 0

    async def build(self, _spec: object) -> None:
        self.builds += 1


@pytest.fixture
def channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, str, StartupEngine]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'webhook.db'}"

    async def schema():
        database = create_db_engine(url)
        async with database.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        await database.dispose()

    asyncio.run(schema())
    engine = StartupEngine()
    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: engine)
    config = (
        Path(__file__).resolve().parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
    )
    app = create_app(str(config), Settings.from_values(database_url=url, **settings_values()))
    with TestClient(app) as client:
        yield client, url, engine


def saved_rows(url, model):
    async def read():
        database = create_db_engine(url)
        try:
            async with session_factory(database)() as session:
                return list((await session.exec(select(model))).all())
        finally:
            await database.dispose()

    return asyncio.run(read())


def test_disabled_webhook_is_absent_without_building_an_engine() -> None:
    app = create_app("unused.yml", Settings.from_values())
    client = TestClient(app)
    assert client.get(ENDPOINT).status_code == 404
    assert client.post(ENDPOINT).status_code == 404


@pytest.mark.parametrize("missing", ["account_id", "phone_number_id", "app_secret", "verify_token"])
def test_enabled_intake_rejects_missing_configuration(missing):
    with pytest.raises(ValidationError):
        Settings.from_values(**settings_values(**{f"extra_whatsapp_{missing}": None}))


@pytest.mark.parametrize(
    "url",
    ["sqlite+aiosqlite:///:memory:", "sqlite://", "sqlite:///file:queue?mode=memory&uri=true"],
)
def test_enabled_intake_requires_durable_storage(url):
    with pytest.raises(ValidationError):
        Settings.from_values(database_url=url, **settings_values())


def test_configuration_secrets_are_redacted_even_on_failure():
    config = Settings.from_values(**settings_values())
    for output in (repr(config), config.model_dump_json()):
        assert APP_SECRET not in output and VERIFY_TOKEN not in output
    with pytest.raises(ValidationError) as failure:
        Settings.from_values(**settings_values(extra_whatsapp_verify_token=APP_SECRET))
    assert APP_SECRET not in str(failure.value)


def test_subscription_returns_exact_plain_text_challenge(channel):
    client, _, engine = channel
    result = client.get(
        ENDPOINT,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "000123+challenge",
        },
    )
    assert result.status_code == 200
    assert result.text == "000123+challenge"
    assert result.headers["content-type"].startswith("text/plain")
    assert engine.builds == 1 and engine.prompts == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN},
    ],
)
def test_invalid_subscription_fails_closed(channel, params):
    client, _, _ = channel
    assert client.get(ENDPOINT, params=params).status_code == 403


@pytest.mark.parametrize(
    "signature", [None, "", "sha1=123", "sha256=123", "sha256=" + "z" * 64, "sha256=" + "0" * 64]
)
def test_unsigned_or_bad_signature_never_reaches_storage(channel, signature):
    client, url, _ = channel
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    assert (
        client.post(
            ENDPOINT, content=encoded(payload(change(message()))), headers=headers
        ).status_code
        == 401
    )
    assert saved_rows(url, ChannelInboxRow) == []


def test_signature_covers_original_bytes_and_rejects_ambiguous_headers(channel):
    client, url, _ = channel
    body = encoded(payload(change(message())))
    assert client.post(ENDPOINT, content=body + b" ", headers=signed(body)).status_code == 401
    headers = [*signed(body).items(), ("X-Hub-Signature-256", signed(body)["X-Hub-Signature-256"])]
    assert client.post(ENDPOINT, content=body, headers=headers).status_code == 401
    assert saved_rows(url, ChannelInboxRow) == []


def test_all_entries_changes_and_messages_are_persisted_without_running_an_agent(channel):
    client, url, engine = channel
    data = payload(change(message("one"), message("two")), change(message("three")))
    data["entry"].insert(0, {"id": "unrelated", "changes": [change(message("foreign"))]})
    data["entry"].append({"id": "account-1", "changes": [change(message("four"))]})
    body = encoded(data)
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    rows = saved_rows(url, ChannelInboxRow)
    assert [row.provider_message_id for row in rows] == ["one", "two", "three", "four"]
    assert rows[0].content == "שלום 👋\nHow can I help?"
    assert rows[0].sender_id == "opaque-sender"
    assert all(
        row.system_id == "Enterprise Knowledge Assistant" and row.state == "queued" for row in rows
    )
    assert saved_rows(url, ConversationSessionRow) == []
    assert engine.builds == 1 and engine.prompts == []


def test_other_receiving_number_is_ignored(channel):
    client, url, _ = channel
    body = encoded(payload(change(message(), phone="other-number")))
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    assert saved_rows(url, ChannelInboxRow) == []


def test_invalid_message_does_not_discard_its_valid_sibling(channel):
    client, url, _ = channel
    body = encoded(payload(change(message("bad", timestamp="not-a-timestamp"), message("good"))))
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    assert [row.provider_message_id for row in saved_rows(url, ChannelInboxRow)] == ["good"]


def test_body_limit_applies_even_without_a_content_length(channel):
    client, url, _ = channel
    body = b" " * 262_145
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 413
    assert (
        client.post(
            ENDPOINT, content=iter([body[:200_000], body[200_000:]]), headers=signed(body)
        ).status_code
        == 413
    )
    assert saved_rows(url, ChannelInboxRow) == []


def test_delivery_status_is_durable_and_does_not_create_agent_input(channel):
    client, url, engine = channel
    status = {
        "id": "outbound-one",
        "recipient_id": "opaque-sender",
        "status": "delivered",
        "timestamp": message()["timestamp"],
        "errors": [{"message": "private provider error"}],
    }
    body = encoded(payload(change(statuses=[status])))
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    from agent_manager.infrastructure.persistence.channels.tables import ChannelDeliveryEventRow

    events = saved_rows(url, ChannelDeliveryEventRow)
    assert [(event.provider_message_id, event.status) for event in events] == [
        ("outbound-one", "delivered")
    ]
    assert "private provider error" not in events[0].model_dump_json()
    assert saved_rows(url, ChannelInboxRow) == [] and engine.prompts == []


@pytest.mark.parametrize(
    "body",
    [b"not JSON", b"[]", b'{"object":"whatsapp_business_account","entry":"invalid"}', b'"invalid"'],
)
def test_signed_poison_callback_is_recorded_before_acknowledgement(channel, body):
    client, url, _ = channel
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    from agent_manager.infrastructure.persistence.channels.tables import ChannelWebhookReceiptRow

    receipts = saved_rows(url, ChannelWebhookReceiptRow)
    assert len(receipts) == 1
    assert receipts[0].issues == [{"location": "body", "reason": "invalid_envelope"}]
    assert receipts[0].body_digest == hashlib.sha256(body).hexdigest()
    assert saved_rows(url, ChannelInboxRow) == []


def test_conflicting_provider_input_is_quarantined_without_rebinding(channel):
    client, url, _ = channel
    first = message()
    original = encoded(payload(change(first)))
    changed = encoded(
        payload(change({**first, "from": "attacker", "text": {"body": "private conflicting text"}}))
    )
    assert client.post(ENDPOINT, content=original, headers=signed(original)).status_code == 200
    assert client.post(ENDPOINT, content=changed, headers=signed(changed)).status_code == 200
    from agent_manager.infrastructure.persistence.channels.tables import (
        ChannelBindingRow,
        ChannelWebhookReceiptRow,
    )

    assert len(saved_rows(url, ChannelInboxRow)) == 1
    assert [row.sender_id for row in saved_rows(url, ChannelBindingRow)] == ["opaque-sender"]
    receipts = saved_rows(url, ChannelWebhookReceiptRow)
    assert receipts[-1].issues[0]["reason"] == "conflicting_message"
    assert "private conflicting text" not in receipts[-1].model_dump_json()


def test_storage_failure_is_retryable_and_never_acknowledged(channel, monkeypatch, caplog):
    client, url, _ = channel

    async def unavailable(*args, **kwargs):
        raise OSError("private-storage-credential")

    from agent_manager.infrastructure.persistence.channels.webhooks import SqlWebhookRepository

    monkeypatch.setattr(SqlWebhookRepository, "accept", unavailable)
    body = encoded(payload(change(message())))
    response = client.post(ENDPOINT, content=body, headers=signed(body))
    assert response.status_code == 503
    assert "private-storage-credential" not in response.text + caplog.text
    assert saved_rows(url, ChannelInboxRow) == []


def test_excessive_content_length_is_rejected_without_integer_conversion_failure(channel):
    client, _, _ = channel
    body = b"{}"
    headers = {**signed(body), "Content-Length": "9" * 5000}
    assert client.post(ENDPOINT, content=body, headers=headers).status_code == 400


def test_failed_commit_returns_retryable_failure_and_leaves_no_receipt(channel):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from agent_manager.infrastructure.persistence.channels.tables import (
        ChannelBindingRow,
        ChannelWebhookReceiptRow,
    )

    client, url, _ = channel

    def reject_commit(session):
        raise OSError("injected commit failure")

    body = encoded(payload(change(message())))
    event.listen(Session, "before_commit", reject_commit)
    try:
        response = client.post(ENDPOINT, content=body, headers=signed(body))
        assert response.status_code == 503
    finally:
        event.remove(Session, "before_commit", reject_commit)
    for model in (ChannelInboxRow, ChannelBindingRow, ChannelWebhookReceiptRow):
        assert saved_rows(url, model) == []
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200
    assert len(saved_rows(url, ChannelInboxRow)) == 1


@pytest.mark.parametrize("headers", [{"Content-Type": "text/plain"}, {"Content-Encoding": "gzip"}])
def test_unsupported_wire_encoding_is_rejected_before_storage(channel, headers):
    client, url, _ = channel
    body = encoded(payload(change(message())))
    response = client.post(ENDPOINT, content=body, headers={**signed(body), **headers})
    assert response.status_code == 415
    assert saved_rows(url, ChannelInboxRow) == []


def test_duplicate_subscription_parameters_are_rejected(channel):
    client, _, _ = channel
    params = [
        ("hub.mode", "subscribe"),
        ("hub.verify_token", VERIFY_TOKEN),
        ("hub.challenge", "one"),
        ("hub.challenge", "two"),
    ]
    assert client.get(ENDPOINT, params=params).status_code == 403


@pytest.mark.parametrize(
    ("request_kind", "reason"),
    [
        ("bad_signature", "signature"),
        ("missing_signature", "signature"),
        ("content_type", "content_type"),
        ("encoding", "encoding"),
        ("subscription", "subscription"),
    ],
)
def test_rejections_log_a_fixed_reason_without_request_content(
    channel, caplog, request_kind, reason
):
    client, _, _ = channel
    caplog.set_level(logging.WARNING, logger="agent_manager.api.routes.whatsapp")
    body = encoded(payload(change(message(text={"body": "private question"}))))
    forged = "sha256=" + "0" * 64
    if request_kind == "subscription":
        params = {"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "c"}
        client.get(ENDPOINT, params=params)
    else:
        headers = {
            "bad_signature": {"Content-Type": "application/json", "X-Hub-Signature-256": forged},
            "missing_signature": {"Content-Type": "application/json"},
            "content_type": {**signed(body), "Content-Type": "text/plain"},
            "encoding": {**signed(body), "Content-Encoding": "gzip"},
        }[request_kind]
        client.post(ENDPOINT, content=body, headers=headers)

    rejected = [r.getMessage() for r in caplog.records if "webhook rejected" in r.getMessage()]
    assert rejected == [f"whatsapp webhook rejected: reason={reason}"]
    for private in ("private question", forged, "wrong-token", APP_SECRET):
        assert private not in caplog.text
