"""FastAPI dependencies. The composition root puts the service on app.state."""

from __future__ import annotations

from fastapi import Request

from agent_manager.application import ConversationService


def get_service(request: Request) -> ConversationService:
    return request.app.state.service
