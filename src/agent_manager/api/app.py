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
from agent_engine.engine.text_completion_engine import TextCompletionEngine
from agent_engine.logging_config import configure_logging
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_manager.api.deps import CallerIdentity
from agent_manager.api.routes import router
from agent_manager.api.web import mount_web
from agent_manager.application import ConversationService
from agent_manager.composition import application_repositories, build_identity_resolver
from agent_manager.config import Settings
from agent_manager.domain import TitleGenerator
from agent_manager.infrastructure.titles import ConversationTitler


def _title_generator(engine: object) -> TitleGenerator | None:
    """A titler backed by the engine's own default model, or none.

    A system with no `defaults.model` simply doesn't title conversations —
    checked once at startup rather than failing per-conversation.
    """
    if isinstance(engine, TextCompletionEngine) and engine.can_complete_text:
        return ConversationTitler(engine)
    return None


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

        async with (
            application_repositories(settings) as repositories,
            LangGraphEngine(
                base_dir,
                session_approval_repository=repositories.session_approvals,
                tool_usage_repository=repositories.tool_usage,
                run_repository=repositories.runs,
            ) as engine,
        ):
            await engine.build(spec)
            service = ConversationService(
                engine,
                repositories.conversations,
                window=settings.context_window,
                max_chars=settings.context_max_chars,
                max_tokens=settings.context_max_tokens,
                snapshot_ttl_seconds=settings.snapshot_ttl_seconds,
                system_name=spec.meta.name,
                config_path=str(Path(config_path).resolve()),
                run_repository=repositories.runs,
                title_generator=_title_generator(engine),
            )
            app.state.service = service
            try:
                yield
            finally:
                await service.close()

    app = FastAPI(lifespan=lifespan)
    app.state.caller_identity = CallerIdentity(
        resolver=build_identity_resolver(settings),
        cookie_name=settings.extra_auth_cookie,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "system": getattr(app.state, "system_name", "")}

    app.include_router(router)
    mount_web(app, settings)
    return app
