"""HTTP routes — the conversation API a chat UI talks to."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.api.deps import get_caller_id, get_service
from agent_manager.api.schemas import (
    ConversationSummary,
    CreateConversationRequest,
    CreateConversationResponse,
    MessageOut,
    SendMessageRequest,
    SendMessageResponse,
    StreamEventOut,
    TokenBudgetResponse,
    ToolRecord,
)
from agent_manager.application import (
    ConversationAccessDenied,
    ConversationAlreadyExists,
    ConversationNotFound,
    ConversationService,
    ConversationTokenBudgetExceeded,
)

router = APIRouter()

Service = Annotated[ConversationService, Depends(get_service)]
CallerId = Annotated[str | None, Depends(get_caller_id)]

_BUDGET_EXCEEDED_DETAIL = {
    "error_type": "context_limit_exceeded",
    "message": ("This conversation has reached its context limit. Start a new chat to continue."),
}

_HTTP_ERRORS: dict[type[Exception], tuple[int, Any]] = {
    ConversationNotFound: (404, "conversation not found"),
    ConversationAccessDenied: (403, "conversation owned by another user"),
    ConversationAlreadyExists: (409, "conversation id already taken"),
    ConversationTokenBudgetExceeded: (429, _BUDGET_EXCEEDED_DETAIL),
}


@contextmanager
def _as_http_error() -> Iterator[None]:
    try:
        yield
    except tuple(_HTTP_ERRORS) as exc:
        status, detail = _HTTP_ERRORS[type(exc)]
        raise HTTPException(status_code=status, detail=detail) from None


@router.post("/conversations", response_model=CreateConversationResponse)
async def create_conversation(
    service: Service, caller_id: CallerId, body: CreateConversationRequest | None = None
) -> CreateConversationResponse:
    body = body or CreateConversationRequest()
    with _as_http_error():
        session_id = await service.create(user_id=caller_id, session_id=body.session_id)
    return CreateConversationResponse(conversation_id=session_id, session_id=session_id)


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(service: Service, caller_id: CallerId) -> list[ConversationSummary]:
    sessions = await service.list_conversations(caller_id)

    return [
        ConversationSummary(
            conversation_id=s.session_id,
            title=s.title,
            last_message_at=s.last_message_at,
        )
        for s in sessions
    ]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: str, service: Service, caller_id: CallerId
) -> list[MessageOut]:
    with _as_http_error():
        msgs = await service.history(conversation_id, caller_id=caller_id)
    return [MessageOut(role=m.role, content=m.content, created_at=m.created_at) for m in msgs]


@router.get("/conversations/{conversation_id}/usage", response_model=TokenBudgetResponse)
async def get_usage(
    conversation_id: str, service: Service, caller_id: CallerId
) -> TokenBudgetResponse:
    with _as_http_error():
        usage = await service.usage(conversation_id, caller_id=caller_id)
    return TokenBudgetResponse(
        used_tokens=usage.used_tokens,
        max_tokens=usage.max_tokens,
        percent=usage.percent,
        severity=usage.severity,
    )


def _client_tool_record(t: Any) -> ToolRecord:
    fields = dataclasses.asdict(t)
    if fields.get("error"):
        fields["error"] = "Tool execution failed"
    return ToolRecord(**fields)


@router.post("/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str, body: SendMessageRequest, service: Service, caller_id: CallerId
) -> SendMessageResponse:
    try:
        with _as_http_error():
            result = await service.send(conversation_id, body.message, caller_id=caller_id)
    except HTTPException:
        raise

    except Exception as exc:  # engine failure
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SendMessageResponse(
        answer=result.answer,
        visited=list(result.visited),
        used_tools=[_client_tool_record(t) for t in result.used_tools],
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
            [_client_tool_record(tool) for tool in event.used_tools] if event.used_tools else None
        ),
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str, body: SendMessageRequest, service: Service, caller_id: CallerId
) -> StreamingResponse:
    stream = service.stream(conversation_id, body.message, caller_id=caller_id)

    try:
        with _as_http_error():
            first = await stream.__anext__()
    except StopAsyncIteration:
        first = None

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
