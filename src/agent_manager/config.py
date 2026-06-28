"""Typed settings, read from the environment (and a .env file)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def normalize_database_url(url: str, backend: str) -> str:
    if backend == "sqlite" and url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
    if backend == "postgres" and url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_db_backend: Literal["sqlite", "postgres"] = "sqlite"
    agent_db_url: str | None = None
    database_url: str = "sqlite+aiosqlite:///chat.db"
    context_window: int = 10
    context_max_chars: int | None = None
    snapshot_ttl_seconds: int = 86_400
    host: str = "0.0.0.0"
    port: int = 8100
    # Deny cross-origin by default; each deployment sets its own site(s),
    # e.g. CORS_ORIGINS=https://acmecorp.com,https://www.acmecorp.com
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v

    @property
    def effective_database_url(self) -> str:
        return normalize_database_url(self.agent_db_url or self.database_url, self.agent_db_backend)
