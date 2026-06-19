"""Tests for before_mcp_request wired through the httpx transport seam.

No real MCP server is touched: HookedMCPAuth is driven directly through its
async_auth_flow against a throwaway httpx.Request — exactly how httpx invokes
it per request (mirrors the MCPAuthLoader tests).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from agent_engine.core.spec import HooksConfig, HookSpec
from agent_engine.runtime.hooks.context import current_run_context
from agent_engine.runtime.hooks.errors import HookExecutionError
from agent_engine.runtime.hooks.manager import HookManager
from agent_engine.runtime.hooks.mcp import HookedMCPAuth
from agent_engine.runtime.hooks.models import RunContext

_FIX = "tests.runtime.hooks.fixtures"


def _manager(*specs: HookSpec) -> HookManager:
    return HookManager.from_config(HooksConfig(hooks=specs))


async def _apply(auth: httpx.Auth, request: httpx.Request | None = None) -> httpx.Request:
    request = request or httpx.Request("POST", "https://internal.test/mcp")
    flow = auth.async_auth_flow(request)
    signed = await flow.__anext__()
    await flow.aclose()
    return signed


async def test_before_mcp_request_adds_authorization_header() -> None:
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_auth_header", {"token": "tok-1"}))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    request = await _apply(auth)
    assert request.headers["authorization"] == "Bearer tok-1"


async def test_before_mcp_request_adds_tenant_header() -> None:
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_tenant_header", {"tenant": "acme"}))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    request = await _apply(auth)
    assert request.headers["x-tenant"] == "acme"


async def test_base_auth_and_hooks_compose() -> None:
    # A plugin (base) auth runs first, hooks add on top.
    class _Base(httpx.Auth):
        async def async_auth_flow(self, request):
            request.headers["X-Base"] = "1"
            yield request

    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_tenant_header", {"tenant": "z"}))
    auth = HookedMCPAuth(mgr, "internal-mcp", base=_Base())
    request = await _apply(auth)
    assert request.headers["x-base"] == "1"
    assert request.headers["x-tenant"] == "z"


async def test_hook_reads_current_run_context() -> None:
    def add_user(context, request, config):
        uid = context.user_id if context else "anon"
        return request.with_headers({"X-User": uid})

    import tests.runtime.hooks.fixtures as fx

    fx.add_user_header = add_user  # type: ignore[attr-defined]
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_user_header"))
    auth = HookedMCPAuth(mgr, "internal-mcp")

    token = current_run_context.set(RunContext(user_id="alice"))
    try:
        request = await _apply(auth)
    finally:
        current_run_context.reset(token)
    assert request.headers["x-user"] == "alice"


async def test_no_hooks_leaves_request_unchanged() -> None:
    mgr = _manager()  # no before_mcp_request hooks
    auth = HookedMCPAuth(mgr, "internal-mcp")
    request = httpx.Request("POST", "https://internal.test/mcp")
    headers_before = dict(request.headers)
    signed = await _apply(auth, request)
    assert signed is request
    assert dict(signed.headers) == headers_before


async def test_hook_failure_prevents_request() -> None:
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:boom"))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    request = httpx.Request("POST", "https://internal.test/mcp")
    flow = auth.async_auth_flow(request)
    with pytest.raises(HookExecutionError):
        await flow.__anext__()


async def test_header_values_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager(
        HookSpec("before_mcp_request", f"{_FIX}:add_auth_header", {"token": "super-secret-xyz"})
    )
    auth = HookedMCPAuth(mgr, "internal-mcp")
    with caplog.at_level(logging.DEBUG):
        await _apply(auth)
    assert "super-secret-xyz" not in caplog.text
    assert "Bearer" not in caplog.text
