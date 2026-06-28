"""HTTP routes — the conversation API a chat UI talks to."""

from __future__ import annotations

import dataclasses
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from agent_manager.api.deps import get_service
from agent_manager.api.schemas import (
    CreateConversationRequest,
    CreateConversationResponse,
    MessageOut,
    SendMessageRequest,
    SendMessageResponse,
    ToolRecord,
)
from agent_manager.application import ConversationNotFound, ConversationService

router = APIRouter()

Service = Annotated[ConversationService, Depends(get_service)]


@router.post("/conversations", response_model=CreateConversationResponse)
async def create_conversation(
    service: Service, body: CreateConversationRequest | None = None
) -> CreateConversationResponse:
    body = body or CreateConversationRequest()
    session_id = await service.create(user_id=body.user_id, session_id=body.session_id)
    return CreateConversationResponse(conversation_id=session_id, session_id=session_id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(conversation_id: str, service: Service) -> list[MessageOut]:
    try:
        msgs = await service.history(conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return [MessageOut(role=m.role, content=m.content, created_at=m.created_at) for m in msgs]


@router.post("/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str, body: SendMessageRequest, service: Service
) -> SendMessageResponse:
    try:
        result = await service.send(conversation_id, body.message, user_id=body.user_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except Exception as exc:  # engine failure
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SendMessageResponse(
        answer=result.answer,
        visited=list(result.visited),
        used_tools=[ToolRecord(**dataclasses.asdict(t)) for t in result.used_tools],
    )
