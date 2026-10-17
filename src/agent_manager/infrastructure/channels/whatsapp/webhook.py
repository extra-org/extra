"""Authenticate, normalize, and durably accept a callback, without executing."""

import hashlib
from datetime import datetime

from agent_manager.domain.channels.webhooks import WebhookReceipt, WebhookRepository
from agent_manager.infrastructure.channels.whatsapp.config import WhatsAppWebhookConfig
from agent_manager.infrastructure.channels.whatsapp.normalization import normalize
from agent_manager.infrastructure.channels.whatsapp.security import verify_signature


class WhatsAppWebhook:
    def __init__(self, config: WhatsAppWebhookConfig, repository: WebhookRepository) -> None:
        self.config = config
        self._repository = repository

    async def receive(
        self, body: bytes, signature: str | None, received_at: datetime
    ) -> WebhookReceipt:
        verify_signature(body, signature, self.config.app_secret.get_secret_value())
        batch = normalize(body, self.config, received_at)
        return await self._repository.accept(
            self.config.connection_id,
            self.config.system_id,
            hashlib.sha256(body).hexdigest(),
            received_at,
            batch,
        )
