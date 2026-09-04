"""Project engine and domain results onto public API response schemas."""

from __future__ import annotations

import dataclasses
from typing import Any

from agent_engine.engine.types import PendingApproval, RunResult
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.api.schemas import (
    PendingApprovalOut,
    SendMessageResponse,
    StreamEventOut,
    ToolRecord,
)


def client_tool_record(tool: Any) -> ToolRecord:
    fields = dataclasses.asdict(tool)
    if fields.get("error"):
        fields["error"] = "Tool execution failed"
    return ToolRecord(**fields)


def pending_approval(approval: PendingApproval | None) -> PendingApprovalOut | None:
    if approval is None:
        return None
    return PendingApprovalOut(
        run_id=approval.run_id,
        approval_id=approval.approval_id,
        agent_id=approval.agent_id,
        tool_name=approval.tool_name,
        description=approval.description,
        provider=approval.provider,
        server_id=approval.server_id,
        arguments=approval.arguments,
    )


def run_response(result: RunResult) -> SendMessageResponse:
    approval = pending_approval(result.pending_approval)
    return SendMessageResponse(
        answer=result.answer,
        visited=list(result.visited),
        used_tools=[client_tool_record(tool) for tool in result.used_tools],
        status=result.status,
        run_id=approval.run_id if approval is not None else None,
        pending_approval=approval,
    )


def to_stream_event(event: RunStreamEvent) -> StreamEventOut:
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
            [client_tool_record(tool) for tool in event.used_tools] if event.used_tools else None
        ),
        run_id=event.run_id,
        message_id=event.message_id,
        approval_id=event.approval_id,
        agent_id=event.agent_id,
        description=event.description,
        arguments=event.arguments,
    )
