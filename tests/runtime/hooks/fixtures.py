"""Importable hook callables used by loader/manager tests.

These exist as a real module so tests can reference them by import path exactly
the way a YAML ``ref`` would (``tests.runtime.hooks.fixtures:sync_hook``).
"""

from __future__ import annotations

from typing import Any

from agent_engine.runtime.hooks.models import (
    HookInvocation,
    McpRequestContext,
    RunContext,
    ToolResultContext,
)

# A module-level sink so side-effect hooks can be observed without globals in
# the test bodies. Tests clear it before use.
CALLS: list[Any] = []


def sync_hook(context: Any) -> None:
    CALLS.append(("sync", context))


async def async_hook(context: Any) -> None:
    CALLS.append(("async", context))


def run_start_enrich(context: RunContext) -> RunContext:
    return context.replace(user_id="alice")


def add_auth_header(context: RunContext | None, request: McpRequestContext) -> McpRequestContext:
    return request.with_headers({"Authorization": "Bearer static"})


def add_tenant_header(context: RunContext | None, request: McpRequestContext) -> McpRequestContext:
    return request.with_headers({"X-Tenant": "acme"})


def leave_mcp_request_unchanged(
    context: RunContext | None, request: McpRequestContext
) -> McpRequestContext:
    return request


def mutate_mcp_request_in_place(
    context: RunContext | None, request: McpRequestContext
) -> McpRequestContext:
    request.headers["X-Tenant"] = "acme"
    return request


def boom(*args: Any) -> None:
    raise RuntimeError("hook exploded")


def secret_boom(*args: Any) -> None:
    raise RuntimeError("secret-context7-key")


class CallableHook:
    """A class hook: instantiated by the loader, called like a function."""

    def __call__(self, context: Any) -> None:
        CALLS.append(("callable", context))


class McpAuthHook:
    """Method-style class hook used to prove one instance is reused."""

    instances_created = 0

    def __init__(self) -> None:
        type(self).instances_created += 1
        self.calls = 0

    async def before_mcp_request(
        self, context: RunContext | None, request: McpRequestContext
    ) -> McpRequestContext:
        self.calls += 1
        CALLS.append(
            (
                "mcp_auth_method",
                id(self),
                self.calls,
                context,
            )
        )
        return request.with_headers({"X-Audience": "internal-docs"})


class MissingConfigConstructorHook:
    def __init__(self, one: object, two: object) -> None:
        self.one = one
        self.two = two

    def before_mcp_request(
        self, context: RunContext | None, request: McpRequestContext
    ) -> McpRequestContext:
        return request


class NonCallableMethodHook:
    before_mcp_request = 123


class ManagedHook:
    instances_created = 0

    def __init__(self) -> None:
        type(self).instances_created += 1
        self.calls = 0

    async def before_mcp_request(self, event: HookInvocation) -> McpRequestContext:
        self.calls += 1
        request = event.payload_as(McpRequestContext)
        CALLS.append(("managed_before_mcp", id(self), self.calls, event))
        return request.with_headers({"Authorization": "Bearer static", "X-Tenant": "acme"})

    def attach_user_context(self, event: HookInvocation) -> RunContext:
        self.calls += 1
        context = event.payload_as(RunContext)
        CALLS.append(("managed_run_start", id(self), self.calls, event))
        return context.replace(user_id="managed-user")

    def audit_warn(self, event: HookInvocation) -> None:
        CALLS.append(("managed_warn", id(self), event))
        raise RuntimeError("audit sink down")


not_callable = 123


# -- engine-integration recording hooks (tagged by point) -------------------


def record_engine_start(context: Any) -> None:
    CALLS.append(("on_engine_start", context))


def record_run_start(context: RunContext) -> RunContext:
    CALLS.append(("on_run_start", context))
    return context.replace(metadata={**context.metadata, "seen": True})


def record_before_tool_call(context: Any, request: Any) -> None:
    CALLS.append(("before_tool_call", request))


def record_after_tool_call(context: Any, call: Any) -> None:
    CALLS.append(("after_tool_call", call))


def record_on_tool_error(context: Any, call: Any) -> None:
    CALLS.append(("on_tool_error", call))


def record_run_end(context: Any, summary: Any) -> None:
    CALLS.append(("on_run_end", summary))


def record_engine_stop(context: Any) -> None:
    CALLS.append(("on_engine_stop", context))


def record_after_mcp_response(context: Any, response: Any) -> None:
    CALLS.append(("after_mcp_response", response))


def record_run_error(context: Any, error: BaseException) -> None:
    CALLS.append(("on_run_error", error))


def truncate_tool_result(
    context: RunContext | None, result: ToolResultContext
) -> ToolResultContext:
    """Record the full result, then truncate it before the engine stores it."""
    CALLS.append(("transform_tool_result", result))
    limit = 3
    if len(result.result) <= limit:
        return result
    return result.with_result(result.result[:limit])
