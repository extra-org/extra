"""Per-run context propagation for hooks.

The engine is created once and shared across requests, so it must
hold no request state. The current run's :class:`RunContext` is instead carried
in a :class:`contextvars.ContextVar`, which is copied into any task/coroutine
spawned during the run. This lets the MCP transport auth layer and tool-call
sites reach the active run's identity without the engine storing it.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from agent_engine.runtime.hooks.models import RunContext

current_run_context: ContextVar[RunContext | None] = ContextVar("current_run_context", default=None)


def public_run_context(ctx: RunContext) -> dict[str, Any]:
    """Project request facts for access filters without exposing credentials."""
    data: dict[str, Any] = {}
    for key in ("run_id", "conversation_id", "user_id", "organization_id"):
        value = getattr(ctx, key)
        if value is not None:
            data[key] = value
    if ctx.metadata:
        data["metadata"] = dict(ctx.metadata)
    if ctx.auth_context is not None:
        auth: dict[str, Any] = {}
        for key in ("user_id", "organization_id"):
            value = getattr(ctx.auth_context, key)
            if value is not None:
                auth[key] = value
        if ctx.auth_context.scopes:
            auth["scopes"] = tuple(ctx.auth_context.scopes)
        if ctx.auth_context.roles:
            auth["roles"] = tuple(ctx.auth_context.roles)
        if ctx.auth_context.metadata:
            auth["metadata"] = dict(ctx.auth_context.metadata)
        if auth:
            data["auth"] = auth
    return data
