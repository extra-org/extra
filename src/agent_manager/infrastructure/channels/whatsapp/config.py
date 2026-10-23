"""Validated, immutable startup target; secret values are never represented."""

from dataclasses import dataclass, field

from pydantic import SecretStr

from agent_manager.domain.channels.models import bounded_identifier


@dataclass(frozen=True)
class WhatsAppWebhookConfig:
    connection_id: str
    system_id: str
    account_id: str
    phone_number_id: str
    app_secret: SecretStr = field(repr=False)
    verify_token: SecretStr = field(repr=False)
    max_body_bytes: int = 262_144

    def __post_init__(self) -> None:
        bounded_identifier(self.connection_id, limit=128)
        bounded_identifier(self.system_id, limit=256)
        bounded_identifier(self.account_id)
        bounded_identifier(self.phone_number_id)
