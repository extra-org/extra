"""HTTP routes — the conversation API a chat UI talks to."""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.api.deps import get_service
from agent_manager.api.schemas import (
    CreateConversationRequest,
    CreateConversationResponse,
    MessageOut,
    SendMessageRequest,
    SendMessageResponse,
    StreamEventOut,
    ToolRecord,
)
from agent_manager.application import (
    ConversationNotFound,
    ConversationService,
    ConversationTokenBudgetExceeded,
)

router = APIRouter()
logger = logging.getLogger(__name__)

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
    except ConversationTokenBudgetExceeded:
        raise HTTPException(status_code=429, detail="conversation token budget exceeded") from None
    except Exception as exc:  # engine failure
        logger.exception("send_message failed")
        raise HTTPException(status_code=500, detail="internal server error") from exc
    return SendMessageResponse(
        answer=result.answer,
        visited=list(result.visited),
        used_tools=[ToolRecord(**dataclasses.asdict(t)) for t in result.used_tools],
    )


def _to_stream_event(event: RunStreamEvent) -> StreamEventOut:
    return StreamEventOut(
        type=event.type,
        content=event.content,
        route=list(event.route) if event.route is not None else None,
        tool_name=event.tool_name,
        provider=event.provider,
        server_id=event.server_id,
        status=event.status,
        error=event.error,
        system_name=event.system_name,
        used_tools=(
            [ToolRecord(**dataclasses.asdict(tool)) for tool in event.used_tools]
            if event.used_tools
            else None
        ),
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str, body: SendMessageRequest, service: Service
) -> StreamingResponse:
    stream = service.stream(conversation_id, body.message, user_id=body.user_id)

    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = None
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except ConversationTokenBudgetExceeded:
        raise HTTPException(status_code=429, detail="conversation token budget exceeded") from None

    async def event_source() -> AsyncIterator[str]:
        try:
            if first is not None:
                payload = _to_stream_event(first).model_dump(exclude_none=True)
                yield f"event: {first.type}\ndata: {json.dumps(payload)}\n\n"
            async for event in stream:
                payload = _to_stream_event(event).model_dump(exclude_none=True)
                yield f"event: {event.type}\ndata: {json.dumps(payload)}\n\n"
        except Exception as exc:
            logger.exception("stream_message failed")
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': 'internal server error'})}\n\n"
        finally:
            yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
