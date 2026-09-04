"""Conversation, message, usage, and message-stream endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.api.deps import Caller, Service
from agent_manager.api.errors import (
    INTERNAL_ERROR_MESSAGE,
    as_http_error,
    as_internal_http_error,
)
from agent_manager.api.presenters import run_response, to_stream_event
from agent_manager.api.schemas import (
    ConversationSummary,
    CreateConversationRequest,
    CreateConversationResponse,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    MessageOut,
    PaginatedConversationsResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamEventOut,
    TokenBudgetResponse,
)
from agent_manager.domain import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, MessageFeedback, PageRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/conversations", response_model=CreateConversationResponse)
async def create_conversation(
    service: Service,
    caller: Caller,
    body: CreateConversationRequest | None = None,
) -> CreateConversationResponse:
    body = body or CreateConversationRequest()
    with as_http_error():
        session_id = await service.create(caller, session_id=body.session_id)
    return CreateConversationResponse(conversation_id=session_id, session_id=session_id)


@router.get("/conversations", response_model=PaginatedConversationsResponse)
async def list_conversations(
    service: Service,
    caller: Caller,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = Query(default=None),
) -> PaginatedConversationsResponse:
    with as_http_error():
        page = PageRequest(limit=limit, cursor=cursor)
        paginated = await service.list_conversations(caller, page=page)
    items = [
        ConversationSummary(
            conversation_id=session.session_id,
            title=session.title,
            last_message_at=session.last_message_at,
        )
        for session in paginated.items
    ]
    return PaginatedConversationsResponse(items=items, next_cursor=paginated.next_cursor)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: str,
    service: Service,
    caller: Caller,
) -> list[MessageOut]:
    with as_http_error():
        messages = await service.history(conversation_id, caller)
    return [
        MessageOut(
            message_id=message.message_id,
            run_id=message.run_id,
            role=message.role,
            content=message.content,
            status=message.status,
            created_at=message.created_at,
            feedback=message.feedback,
        )
        for message in messages
    ]


@router.get("/conversations/{conversation_id}/usage", response_model=TokenBudgetResponse)
async def get_usage(
    conversation_id: str,
    service: Service,
    caller: Caller,
) -> TokenBudgetResponse:
    with as_http_error():
        usage = await service.usage(conversation_id, caller)
    return TokenBudgetResponse(
        used_tokens=usage.used_tokens,
        max_tokens=usage.max_tokens,
        percent=usage.percent,
        severity=usage.severity,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackResponse,
)
async def set_message_feedback(
    conversation_id: str,
    message_id: str,
    body: MessageFeedbackRequest,
    service: Service,
    caller: Caller,
) -> MessageFeedbackResponse:
    with as_http_error():
        updated = await service.set_message_feedback(
            conversation_id, message_id, MessageFeedback(body.feedback), caller
        )
    if updated is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="message not found")
    return MessageFeedbackResponse(message_id=updated.message_id, feedback=updated.feedback)


@router.post("/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    service: Service,
    caller: Caller,
) -> SendMessageResponse:
    with as_internal_http_error(logger, "conversation request failed"), as_http_error():
        result = await service.send(
            conversation_id,
            body.message,
            caller,
            edit_message_id=body.edit_message_id,
        )
    return run_response(result)


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    body: SendMessageRequest,
    service: Service,
    caller: Caller,
) -> StreamingResponse:
    with as_http_error():
        turn = await service.prepare_turn(
            conversation_id,
            body.message,
            caller,
            edit_message_id=body.edit_message_id,
        )
    stream = cast(
        AsyncGenerator[RunStreamEvent, None],
        service.stream_turn(turn),
    )

    async def event_source() -> AsyncIterator[str]:
        suspended = False
        exhausted = False
        try:
            started = StreamEventOut(
                type="turn_started",
                run_id=turn.run_id,
                message_id=turn.message_id,
            ).model_dump(exclude_none=True)
            yield f"event: turn_started\ndata: {json.dumps(started)}\n\n"
            async for event in stream:
                suspended = suspended or event.type == "pending_approval"
                payload = to_stream_event(event).model_dump(exclude_none=True)
                yield f"event: {event.type}\ndata: {json.dumps(payload)}\n\n"
            exhausted = True
            # Keep this response open for the independent title result. The
            # client treats `final`/`pending_approval` as terminal for the main
            # execution, so a slow title cannot hold the composer locked.
            try:
                title = await service.wait_for_generated_title(turn)
            except Exception:
                # The engine stream is already terminal and durable. A defect
                # in secondary title delivery must not rewrite that outcome as
                # a failed conversation turn.
                logger.exception("conversation title delivery failed")
                title = None
            if title is not None:
                titled = StreamEventOut(type="title", title=title).model_dump(exclude_none=True)
                yield f"event: title\ndata: {json.dumps(titled)}\n\n"
        except Exception:
            await service.fail_turn(turn)
            # The run is already terminal; `finally` must not try to cancel it.
            exhausted = True
            logger.exception("conversation stream failed")
            payload = {"type": "error", "error": INTERNAL_ERROR_MESSAGE}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        finally:
            # StreamingResponse cancellation closes the application-owned
            # generator, which in turn cancels and awaits the graph producer.
            try:
                await stream.aclose()
            finally:
                if not exhausted and not suspended:
                    await service.cancel_turn(turn)
        yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
