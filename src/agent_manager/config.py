"""Typed settings, read from the environment (and a .env file)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///chat.db"
    context_window: int = 10
    host: str = "0.0.0.0"
    port: int = 8100
