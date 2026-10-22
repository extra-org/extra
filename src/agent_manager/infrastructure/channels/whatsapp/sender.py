"""A bounded single-attempt HTTPS adapter. Never log provider response bodies."""

import json
import re
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from agent_manager.domain.channels.delivery import ChannelSender, SendOutcome
from agent_manager.domain.channels.models import bounded_identifier, bounded_text


class WhatsAppSender(ChannelSender):
    def __init__(
        self,
        version: str,
        phone_number_id: str,
        token: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if re.fullmatch(r"v[1-9][0-9]{0,2}\.0", version) is None:
            raise ValueError("WhatsApp sender requires an explicit Graph API version")
        bounded_identifier(phone_number_id)
        if re.fullmatch(r"[A-Za-z0-9_-]+", phone_number_id) is None:
            raise ValueError("invalid WhatsApp sending number identifier")
        if not token.get_secret_value().strip():
            raise ValueError("WhatsApp sender requires an access token")
        self._url = (
            f"https://graph.facebook.com/{version}/{quote(phone_number_id, safe='')}/messages"
        )
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5, pool=5),
            follow_redirects=False,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, recipient: str, text: str) -> SendOutcome:
        bounded_identifier(recipient)
        bounded_text(text)
        if not text or len(text.encode("utf-16-le")) // 2 > 4096:
            raise ValueError("invalid WhatsApp provider text part")
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "text",
                    "text": {"body": text, "preview_url": False},
                },
                follow_redirects=False,
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 65_536:
                        return SendOutcome("unknown")
                    body.extend(chunk)
                return _outcome(
                    response.status_code, bytes(body), response.headers.get("Retry-After", "")
                )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            return SendOutcome("retry")
        except httpx.RequestError:
            return SendOutcome("unknown")


def _outcome(status: int, body: bytes, retry_after: str) -> SendOutcome:
    try:
        data = json.loads(body)
        if 200 <= status < 300:
            messages = data["messages"]
            if not isinstance(messages, list) or len(messages) != 1:
                return SendOutcome("unknown")
            provider_id = messages[0]["id"]
            if not isinstance(provider_id, str):
                return SendOutcome("unknown")
            bounded_identifier(provider_id)
            return SendOutcome("accepted", provider_id)
        error = data.get("error") if isinstance(data, dict) else None
        known_rejection = isinstance(error, dict) and type(error.get("code")) is int
        if known_rejection and (
            status == 429
            or 500 <= status < 600
            or (status == 400 and isinstance(error, dict) and error.get("is_transient") is True)
        ):
            delay = (
                int(retry_after)
                if len(retry_after) <= 3 and retry_after.isascii() and retry_after.isdecimal()
                else 0
            )
            return SendOutcome("retry", retry_after=min(delay, 300))
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    return SendOutcome("unknown" if status >= 500 or 200 <= status < 300 else "failed")
