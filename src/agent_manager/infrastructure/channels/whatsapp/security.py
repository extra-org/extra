"""Byte-level Meta authentication, independent of browser/user credentials."""

import hashlib
import hmac
import re


class WebhookAuthenticationError(PermissionError):
    """The provider request could not be authenticated."""


def verify_signature(body: bytes, signature: str | None, app_secret: str) -> None:
    if signature is None or re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature) is None:
        raise WebhookAuthenticationError("invalid webhook signature")
    supplied = bytes.fromhex(signature.removeprefix("sha256="))
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied, expected):
        raise WebhookAuthenticationError("invalid webhook signature")


def subscription_challenge(
    mode: str | None, token: str | None, challenge: str | None, expected: str
) -> str:
    if (
        mode != "subscribe"
        or token is None
        or challenge is None
        or not challenge
        or len(challenge.encode()) > 1024
        or not hmac.compare_digest(token.encode(), expected.encode())
    ):
        raise WebhookAuthenticationError("invalid webhook subscription")
    return challenge
