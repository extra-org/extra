from __future__ import annotations

from agent_engine.runtime.hooks import RunContext, ToolCallContext, ToolRequestContext


def before_tool_call(_context: RunContext | None, request: ToolRequestContext) -> None:
    session_id = _context.conversation_id if _context else None
    print(
        f"\n[TOOL EXECUTION] session_id={session_id or '-'} "
        f"tool={request.server_id or request.provider}.{request.tool_name} "
        "executed=true phase=started"
    )


def after_tool_call(_context: RunContext | None, call: ToolCallContext) -> None:
    session_id = _context.conversation_id if _context else None
    print(
        f"[TOOL EXECUTION] session_id={session_id or '-'} "
        f"tool={call.server_id or call.provider}.{call.tool_name} "
        f"executed=true phase=completed status={call.status}"
    )


def on_tool_error(_context: RunContext | None, call: ToolCallContext) -> None:
    session_id = _context.conversation_id if _context else None
    print(
        f"[TOOL EXECUTION] session_id={session_id or '-'} "
        f"tool={call.server_id or call.provider}.{call.tool_name} "
        f"executed=true phase=failed status={call.status}"
    )
