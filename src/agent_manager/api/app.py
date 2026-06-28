"""Composition: build the engine + repository + service, wire them, expose HTTP.

Mirrors the engine API's lifespan pattern (`agent_engine/api/app.py`): the engine
is built once at startup and reused.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.logging_config import configure_logging
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_manager.api.routes import router
from agent_manager.api.web import mount_web
from agent_manager.application import ConversationService
from agent_manager.config import Settings
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository


def create_app(config_path: str, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        configure_logging()
        base_dir = Path(config_path).resolve().parent
        if str(base_dir) not in sys.path:
            sys.path.insert(0, str(base_dir))

        spec = YAMLParser().parse(config_path)
        errors = SystemSpecValidator().validate(spec, base_dir)
        if errors:
            raise RuntimeError(f"Invalid config: {'; '.join(str(e) for e in errors)}")
        app.state.system_name = spec.meta.name

        db_engine = create_db_engine(settings.effective_database_url)
        repository = SqlRepository(session_factory(db_engine))

        async with LangGraphEngine(base_dir) as engine:
            await engine.build(spec)
            app.state.service = ConversationService(
                engine,
                repository,
                window=settings.context_window,
                max_chars=settings.context_max_chars,
                snapshot_ttl_seconds=settings.snapshot_ttl_seconds,
                system_name=spec.meta.name,
                config_path=str(Path(config_path).resolve()),
            )
            yield
        await db_engine.dispose()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "system": getattr(app.state, "system_name", "")}

    app.include_router(router)
    mount_web(app, settings)
    return app
