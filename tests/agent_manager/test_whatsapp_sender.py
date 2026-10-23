"""Wire format and safe send outcomes through httpx's fake transport."""

import json

import httpx
import pytest
from pydantic import SecretStr

from agent_manager.infrastructure.channels.whatsapp.sender import WhatsAppSender


async def test_sender_uses_pinned_https_version_and_preserves_text():
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.accepted"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        sender = WhatsAppSender("v99.0", "123", SecretStr("test-only-token"), client=client)
        outcome = await sender.send("opaque-recipient", "שלום 👋\nlink https://example.test")
    assert outcome.kind == "accepted" and outcome.provider_message_id == "wamid.accepted"
    assert str(requests[0].url) == "https://graph.facebook.com/v99.0/123/messages"
    assert requests[0].headers["authorization"] == "Bearer test-only-token"
    assert json.loads(requests[0].content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "opaque-recipient",
        "type": "text",
        "text": {"body": "שלום 👋\nlink https://example.test", "preview_url": False},
    }
    assert "test-only-token" not in repr(sender)


@pytest.mark.parametrize(
    "status,body,kind",
    [
        (400, {"error": {"code": 1, "is_transient": True}}, "retry"),
        (429, {"error": {"code": 130429}}, "retry"),
        (503, {"error": {"code": 2}}, "retry"),
        (401, {"error": {"code": 190}}, "failed"),
        (400, {"error": {"code": 100}}, "failed"),
        (503, "private provider error", "unknown"),
        (200, {"messages": []}, "unknown"),
        (200, {"messages": [{"id": ""}]}, "unknown"),
        (302, {"error": {"code": 1}}, "failed"),
    ],
)
async def test_sender_classifies_explicit_rejection_and_ambiguous_responses(status, body, kind):
    def provider(request):
        return httpx.Response(status, json=body, headers={"Retry-After": "7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        sender = WhatsAppSender("v99.0", "123", SecretStr("test-only-token"), client=client)
        outcome = await sender.send("recipient", "hello")
    assert outcome.kind == kind
    assert "private" not in repr(outcome)
    if kind == "retry":
        assert outcome.retry_after == 7


@pytest.mark.parametrize(
    "error,kind",
    [
        (httpx.ConnectTimeout, "retry"),
        (httpx.ConnectError, "retry"),
        (httpx.PoolTimeout, "retry"),
        (httpx.ReadTimeout, "unknown"),
        (httpx.WriteTimeout, "unknown"),
        (httpx.RemoteProtocolError, "unknown"),
    ],
)
async def test_sender_never_blindly_retries_a_possibly_submitted_request(error, kind):
    def provider(request):
        raise error("private-provider-credential", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        sender = WhatsAppSender("v99.0", "123", SecretStr("test-only-token"), client=client)
        result = await sender.send("recipient", "hello")
    assert result.kind == kind
    assert "private" not in repr(result)


async def test_sender_bounds_response_bytes():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 65_537))
    ) as client:
        result = await WhatsAppSender(
            "v99.0", "123", SecretStr("test-only-token"), client=client
        ).send("recipient", "hello")
    assert result.kind == "unknown"


@pytest.mark.parametrize("version", ["latest", "v1.0/path", "v0.0", "https://evil.test"])
def test_sender_rejects_unpinned_or_injectable_versions(version):
    with pytest.raises(ValueError):
        WhatsAppSender(version, "123", SecretStr("test-only-token"))


@pytest.mark.parametrize("body", ["", "\x00", "x" * 4097])
async def test_sender_rejects_invalid_bodies_before_any_network_io(body):
    def provider(request):
        pytest.fail("invalid body reached provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        sender = WhatsAppSender("v99.0", "123", SecretStr("test-only-token"), client=client)
        with pytest.raises(ValueError):
            await sender.send("recipient", body)
