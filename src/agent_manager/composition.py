"""Application-level repository composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agent_engine.approvals.session_store import (
    InMemorySessionApprovalRepository,
    SessionApprovalRepository,
)
from agent_manager.config import Settings
from agent_manager.domain import Repository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository


@dataclass(frozen=True)
class ApplicationRepositories:
    conversations: Repository
    session_approvals: SessionApprovalRepository


def build_session_approval_repository() -> SessionApprovalRepository:
    """Create the process-lifetime adapter used by the current application."""
    return InMemorySessionApprovalRepository()


@asynccontextmanager
async def application_repositories(
    settings: Settings,
) -> AsyncIterator[ApplicationRepositories]:
    """Own application repositories for one complete process lifespan."""
    db_engine = create_db_engine(settings.effective_database_url)
    sessions = session_factory(db_engine)
    try:
        yield ApplicationRepositories(
            conversations=SqlRepository(sessions),
            session_approvals=build_session_approval_repository(),
        )
    finally:
        await db_engine.dispose()
