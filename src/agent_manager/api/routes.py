"""HTTP routes — the conversation API a chat UI talks to."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.api.deps import get_service
from agent_manager.api.schemas import (
    ContextUsageResponse,
    ConversationSummary,
    CreateConversationRequest,
    CreateConversationResponse,
    FeedbackRequest,
    FeedbackResponse,
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
    MessageNotFound,
)
from agent_manager.domain import Role

router = APIRouter()

Service = Annotated[ConversationService, Depends(get_service)]


@router.post("/conversations", response_model=CreateConversationResponse)
async def create_conversation(
    service: Service, body: CreateConversationRequest | None = None
) -> CreateConversationResponse:
    body = body or CreateConversationRequest()
    session_id = await service.create(user_id=body.user_id, session_id=body.session_id)
    return CreateConversationResponse(conversation_id=session_id, session_id=session_id)


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(service: Service, user_id: str) -> list[ConversationSummary]:
    sessions = await service.list_conversations(user_id)
    return [
        ConversationSummary(
            conversation_id=s.session_id,
            title=s.title,
            last_message_at=s.last_message_at,
        )
        for s in sessions
    ]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(conversation_id: str, service: Service) -> list[MessageOut]:
    try:
        msgs = await service.history(conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return [
        MessageOut(
            message_id=m.message_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
            feedback=m.feedback,
        )
        for m in msgs
    ]


@router.get("/conversations/{conversation_id}/usage", response_model=ContextUsageResponse)
async def get_usage(conversation_id: str, service: Service) -> ContextUsageResponse:
    try:
        usage = await service.usage(conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return ContextUsageResponse(
        used_tokens=usage.used_tokens,
        max_tokens=usage.max_tokens,
        percent=usage.percent,
        severity=usage.severity,
    )


@router.post("/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str, body: SendMessageRequest, service: Service
) -> SendMessageResponse:
    try:
        result = await service.send(conversation_id, body.message, user_id=body.user_id)
        msgs = await service.history(conversation_id)
        last_msg = msgs[-1] if msgs and msgs[-1].role == Role.ASSISTANT else None
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except ConversationTokenBudgetExceeded:
        raise HTTPException(status_code=429, detail="conversation token budget exceeded") from None
    except Exception as exc:  # engine failure
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SendMessageResponse(
        answer=result.answer,
        visited=list(result.visited),
        used_tools=[ToolRecord(**dataclasses.asdict(t)) for t in result.used_tools],
        message_id=last_msg.message_id if last_msg else None,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
)
async def record_feedback(
    conversation_id: str,
    message_id: str,
    body: FeedbackRequest,
    service: Service,
) -> FeedbackResponse:
    try:
        msg = await service.record_feedback(conversation_id, message_id, body.feedback)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except MessageNotFound as exc:
        raise HTTPException(status_code=404, detail="message not found") from exc
    return FeedbackResponse(message_id=msg.message_id, feedback=msg.feedback)


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
        message_id=event.message_id,
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
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
        finally:
            yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
