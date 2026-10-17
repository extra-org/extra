"""Bounded provider HTTP edge. Durable intake is the only acknowledgement."""

import logging
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.requests import ClientDisconnect

from agent_manager.infrastructure.channels.whatsapp.security import (
    WebhookAuthenticationError,
    subscription_challenge,
)
from agent_manager.infrastructure.channels.whatsapp.webhook import WhatsAppWebhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels/whatsapp", tags=["whatsapp"])


def _rejected(status_code: int, detail: str, reason: str) -> HTTPException:
    """Log a fixed reason only; request content never reaches ordinary logs."""
    logger.warning("whatsapp webhook rejected: reason=%s", reason)
    return HTTPException(status_code=status_code, detail=detail)


def _webhook(request: Request) -> WhatsAppWebhook:
    webhook = getattr(request.app.state, "whatsapp_webhook", None)
    if webhook is None:
        raise HTTPException(status_code=503, detail="webhook intake unavailable")
    return cast(WhatsAppWebhook, webhook)


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_subscription(request: Request) -> PlainTextResponse:
    webhook = _webhook(request)
    names = ("hub.mode", "hub.verify_token", "hub.challenge")
    if any(len(request.query_params.getlist(name)) != 1 for name in names):
        raise _rejected(403, "invalid webhook subscription", "subscription")
    try:
        challenge = subscription_challenge(
            request.query_params.get("hub.mode"),
            request.query_params.get("hub.verify_token"),
            request.query_params.get("hub.challenge"),
            expected=webhook.config.verify_token.get_secret_value(),
        )
    except WebhookAuthenticationError:
        raise _rejected(403, "invalid webhook subscription", "subscription") from None
    return PlainTextResponse(challenge)


@router.post("/webhook")
async def receive_notification(request: Request) -> dict[str, str]:
    webhook = _webhook(request)
    signatures = request.headers.getlist("X-Hub-Signature-256")
    if len(signatures) != 1:
        raise _rejected(401, "invalid webhook signature", "signature")
    if request.headers.get("content-encoding", "identity") != "identity":
        raise _rejected(415, "encoded webhook bodies are unsupported", "encoding")
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise _rejected(415, "webhook requires application/json", "content_type")
    body = await _bounded_body(request, webhook.config.max_body_bytes)
    try:
        await webhook.receive(body, signatures[0], datetime.now(UTC))
    except WebhookAuthenticationError:
        raise _rejected(401, "invalid webhook signature", "signature") from None
    except Exception as exc:
        # The failure boundary must never reflect payloads, identifiers, or DB
        # exception text into provider responses or ordinary logs.
        logger.warning("webhook intake failed: type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="webhook storage unavailable", headers={"Retry-After": "1"}
        ) from None
    return {"status": "ok"}


async def _bounded_body(request: Request, limit: int) -> bytes:
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or (
        lengths and (len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdecimal())
    ):
        raise _rejected(400, "invalid content length", "length")
    if lengths and int(lengths[0]) > limit:
        raise _rejected(413, "webhook body too large", "size")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise _rejected(413, "webhook body too large", "size")
            body.extend(chunk)
    except ClientDisconnect:
        raise _rejected(400, "incomplete webhook body", "incomplete") from None
    return bytes(body)
