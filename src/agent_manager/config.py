"""Typed settings, read from the environment (and a .env file)."""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, NamedTuple
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ONE_HOUR_SECONDS = 3_600
THIRTY_DAYS_SECONDS = 2_592_000


class _EnvAlias(NamedTuple):
    """Maps one deprecated environment variable name to its canonical replacement."""

    old: str  # e.g. "AGENT_AUTH_MODE"  — the legacy env var operators may still have set
    new: str  # e.g. "EXTRA_AUTH_MODE"  — the canonical env var they should migrate to
    field: str  # e.g. "extra_auth_mode"  — the pydantic Settings field name


_DEPRECATED_ENV_VARS: tuple[_EnvAlias, ...] = (
    _EnvAlias("AGENT_DB_BACKEND", "EXTRA_DB_BACKEND", "extra_db_backend"),
    _EnvAlias("AGENT_DB_URL", "EXTRA_DB_URL", "extra_db_url"),
    _EnvAlias("AGENT_AUTH_MODE", "EXTRA_AUTH_MODE", "extra_auth_mode"),
    _EnvAlias("AGENT_AUTH_SECRET", "EXTRA_AUTH_SECRET", "extra_auth_secret"),
    _EnvAlias(
        "AGENT_AUTH_ANONYMOUS_SECRET", "EXTRA_AUTH_ANONYMOUS_SECRET", "extra_auth_anonymous_secret"
    ),
    _EnvAlias(
        "AGENT_AUTH_ANONYMOUS_TTL_SECONDS",
        "EXTRA_AUTH_ANONYMOUS_TTL_SECONDS",
        "extra_auth_anonymous_ttl_seconds",
    ),
    _EnvAlias(
        "AGENT_AUTH_MAX_TTL_SECONDS", "EXTRA_AUTH_MAX_TTL_SECONDS", "extra_auth_max_ttl_seconds"
    ),
    _EnvAlias("AGENT_AUTH_ISSUER", "EXTRA_AUTH_ISSUER", "extra_auth_issuer"),
    _EnvAlias("AGENT_AUTH_AUDIENCE", "EXTRA_AUTH_AUDIENCE", "extra_auth_audience"),
    _EnvAlias("AGENT_AUTH_COOKIE", "EXTRA_AUTH_COOKIE", "extra_auth_cookie"),
    _EnvAlias("AGENT_AUTH_CLAIM_USER_ID", "EXTRA_AUTH_CLAIM_USER_ID", "extra_auth_claim_user_id"),
    _EnvAlias("AGENT_AUTH_CLAIM_EMAIL", "EXTRA_AUTH_CLAIM_EMAIL", "extra_auth_claim_email"),
    _EnvAlias(
        "AGENT_AUTH_CLAIM_DISPLAY_NAME",
        "EXTRA_AUTH_CLAIM_DISPLAY_NAME",
        "extra_auth_claim_display_name",
    ),
    _EnvAlias("AGENT_AUTH_CLAIM_ROLES", "EXTRA_AUTH_CLAIM_ROLES", "extra_auth_claim_roles"),
    _EnvAlias(
        "AGENT_AUTH_CLAIM_ORGANIZATION_ID",
        "EXTRA_AUTH_CLAIM_ORGANIZATION_ID",
        "extra_auth_claim_organization_id",
    ),
)


class AuthMode(StrEnum):
    """How the host product proves who the caller is — the single knob a
    deployment turns, selecting a token policy rather than restating it."""

    ANONYMOUS = "anonymous"
    """No host identity. Every visitor gets a manager-issued pass."""

    HOST_TOKEN = "host_token"
    """Verify the host's own session token. Its lifetime is the host's call."""

    MINT = "mint"
    """The host mints a dedicated short-lived token for us, TTL capped here."""


def normalize_database_url(url: str, backend: str) -> str:
    if backend == "sqlite":
        if url.startswith("sqlite:///"):
            return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
        if not url.startswith("sqlite+"):
            raise ValueError(
                f"EXTRA_DB_URL {url!r} does not look like a sqlite URL "
                f"(expected 'sqlite:///' or 'sqlite+aiosqlite:///')."
            )
    if backend == "postgres":
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        if not url.startswith("postgresql+"):
            raise ValueError(
                f"EXTRA_DB_URL {url!r} does not look like a postgresql URL "
                f"(expected 'postgresql://' or 'postgresql+asyncpg://')."
            )
    return url


def _query_mode(query: Mapping[str, Any]) -> str | None:
    value = query.get("mode")
    if isinstance(value, list | tuple):
        return str(value[0]) if value else None
    return None if value is None else str(value)


def _is_in_memory_sqlite(url: str) -> bool:
    """Whether ``url`` names a SQLite database that lives only in process memory.

    Parsed rather than substring-matched, so an on-disk path containing
    ``:memory:`` keeps its persistence.
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        return False
    if parsed.get_backend_name() != "sqlite":
        return False
    database = parsed.database
    if not database or database == ":memory:":
        return True
    if _query_mode(parsed.query) == "memory":
        return True
    if database.startswith("file:"):
        # A SQLite URI filename carries its own query, e.g. `file:x?mode=memory`.
        uri = urlsplit(database)
        return uri.path == ":memory:" or _query_mode(parse_qs(uri.query)) == "memory"
    return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    extra_db_backend: Literal["sqlite", "postgres"] = "sqlite"
    extra_db_url: str | None = None
    database_url: str = "sqlite+aiosqlite:///chat.db"
    context_window: int = 10
    context_max_chars: int | None = None
    context_max_tokens: int | None = None
    snapshot_ttl_seconds: int = 86_400
    host: str = "0.0.0.0"
    port: int = 8100
    # Deny cross-origin by default; each deployment sets its own site(s),
    # e.g. CORS_ORIGINS=https://acmecorp.com,https://www.acmecorp.com
    cors_origins: Annotated[list[str], NoDecode] = []

    extra_auth_mode: AuthMode = AuthMode.ANONYMOUS
    extra_auth_secret: str | None = None
    # Derived from `extra_auth_secret` when unset, so visitor passes are never
    # signed with the host's key.
    extra_auth_anonymous_secret: str | None = None
    extra_auth_anonymous_ttl_seconds: int = THIRTY_DAYS_SECONDS
    extra_auth_max_ttl_seconds: int = ONE_HOUR_SECONDS
    extra_auth_issuer: str | None = None
    extra_auth_audience: str | None = None
    # Name of the host's session cookie. Only reachable when the manager is
    # served from the host's own origin — see `get_principal`.
    extra_auth_cookie: str | None = None
    extra_auth_claim_user_id: str = "sub"
    extra_auth_claim_email: str = "email"
    extra_auth_claim_display_name: str = "name"
    extra_auth_claim_roles: str = "roles"
    extra_auth_claim_organization_id: str = "org_id"

    extra_whatsapp_enabled: bool = False
    extra_whatsapp_connection_id: str = "whatsapp"
    extra_whatsapp_account_id: str | None = None
    extra_whatsapp_phone_number_id: str | None = None
    extra_whatsapp_app_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)
    extra_whatsapp_verify_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    extra_whatsapp_max_body_bytes: int = Field(default=262_144, ge=1024, le=1_048_576)
    extra_whatsapp_worker_enabled: bool = False
    extra_whatsapp_organization_id: str | None = None
    extra_whatsapp_identity_resolver: str | None = None
    extra_whatsapp_worker_concurrency: int = Field(default=4, ge=1, le=16)
    extra_whatsapp_worker_poll_seconds: float = Field(default=0.5, gt=0, le=60, allow_inf_nan=False)
    extra_whatsapp_worker_turn_seconds: float = Field(
        default=120, gt=0, le=3600, allow_inf_nan=False
    )
    extra_whatsapp_worker_drain_seconds: float = Field(default=15, gt=0, le=60, allow_inf_nan=False)
    extra_whatsapp_sender_enabled: bool = False
    extra_whatsapp_api_version: str | None = None
    extra_whatsapp_access_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    extra_whatsapp_support_contact: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _backfill_deprecated_agent_vars(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        for alias in _DEPRECATED_ENV_VARS:
            if alias.field in data:
                continue
            # Also accept the deprecated name as a Python kwarg (e.g. in tests or
            # programmatic callers that haven't migrated yet).
            old_key = alias.old.lower()
            if old_key in data:
                warnings.warn(
                    f"{alias.old} is deprecated; rename it to {alias.new}.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                data[alias.field] = data[old_key]
                continue
            old_val = os.getenv(alias.old)
            if old_val is not None:
                warnings.warn(
                    f"{alias.old} is deprecated; rename it to {alias.new}.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                data[alias.field] = old_val
        return data

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v

    @model_validator(mode="after")
    def _require_secret_for_host_identity(self) -> Settings:
        if self.extra_auth_mode is not AuthMode.ANONYMOUS and not self.extra_auth_secret:
            raise ValueError(
                f"EXTRA_AUTH_SECRET is required when EXTRA_AUTH_MODE={self.extra_auth_mode}"
            )
        return self

    @model_validator(mode="after")
    def _validate_whatsapp_intake(self) -> Settings:
        if self.extra_whatsapp_sender_enabled:
            import re

            from agent_manager.domain.channels.models import bounded_identifier

            if not self.extra_whatsapp_worker_enabled:
                raise ValueError("WhatsApp sender requires the supervised worker")
            version = self.extra_whatsapp_api_version
            if version is None or re.fullmatch(r"v[1-9][0-9]{0,2}\.0", version) is None:
                raise ValueError("EXTRA_WHATSAPP_API_VERSION must pin a Graph API version")
            access = self.extra_whatsapp_access_token
            if access is None or not access.get_secret_value().strip():
                raise ValueError("EXTRA_WHATSAPP_ACCESS_TOKEN is required for sending")
            for other in (self.extra_whatsapp_app_secret, self.extra_whatsapp_verify_token):
                if other is not None and other.get_secret_value() == access.get_secret_value():
                    raise ValueError("WhatsApp access token must differ from webhook secrets")
            if self.extra_whatsapp_support_contact is None:
                raise ValueError("EXTRA_WHATSAPP_SUPPORT_CONTACT is required for sending")
            bounded_identifier(self.extra_whatsapp_support_contact)
        if self.extra_whatsapp_worker_enabled:
            import re

            from agent_manager.domain.channels.models import bounded_identifier

            if not self.extra_whatsapp_enabled:
                raise ValueError("WhatsApp worker requires enabled intake")
            if not self.extra_whatsapp_organization_id:
                raise ValueError("EXTRA_WHATSAPP_ORGANIZATION_ID is required for the worker")
            bounded_identifier(self.extra_whatsapp_organization_id)
            plugin = self.extra_whatsapp_identity_resolver
            if (
                not plugin
                or len(plugin) > 512
                or re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", plugin) is None
            ):
                raise ValueError("EXTRA_WHATSAPP_IDENTITY_RESOLVER must name module:Class")
        if not self.extra_whatsapp_enabled:
            return self
        from agent_manager.domain.channels.models import bounded_identifier

        bounded_identifier(self.extra_whatsapp_connection_id, limit=128)
        for name in ("account_id", "phone_number_id"):
            value = getattr(self, f"extra_whatsapp_{name}")
            if not value:
                raise ValueError(f"EXTRA_WHATSAPP_{name.upper()} is required for intake")
            bounded_identifier(value)
        secret = self.extra_whatsapp_app_secret
        token = self.extra_whatsapp_verify_token
        if secret is None or not secret.get_secret_value().strip():
            raise ValueError("EXTRA_WHATSAPP_APP_SECRET is required for intake")
        if token is None or not token.get_secret_value().strip():
            raise ValueError("EXTRA_WHATSAPP_VERIFY_TOKEN is required for intake")
        if secret.get_secret_value() == token.get_secret_value():
            raise ValueError("WhatsApp verification token must differ from the app secret")
        if self.uses_process_memory:
            raise ValueError("WhatsApp intake requires persistent SQL storage")
        return self

    @classmethod
    def from_values(cls, **values: Any) -> Settings:
        """Explicit values only, ignoring any local `.env`, so callers that must
        be deterministic do not inherit a developer's file."""
        return cls(_env_file=None, **values)  # type: ignore[call-arg]

    @property
    def effective_database_url(self) -> str:
        return normalize_database_url(self.extra_db_url or self.database_url, self.extra_db_backend)

    @property
    def uses_process_memory(self) -> bool:
        """True only for a SQLite URL that names no on-disk database."""
        return _is_in_memory_sqlite(self.effective_database_url)
