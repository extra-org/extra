"""Typed settings, read from the environment (and a .env file)."""

from __future__ import annotations

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///chat.db"
    context_window: int = 10
    host: str = "0.0.0.0"
    port: int = 8100
    # Deny cross-origin by default; each deployment sets its own site(s),
    # e.g. CORS_ORIGINS=https://acmecorp.com,https://www.acmecorp.com
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v
