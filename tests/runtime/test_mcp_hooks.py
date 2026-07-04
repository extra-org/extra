"""Tests for before_mcp_request wired through the httpx transport seam.

No real MCP server is touched: HookedMCPAuth is driven directly through its
async_auth_flow against a throwaway httpx.Request — exactly how httpx invokes
it per request (mirrors the MCPAuthLoader tests).
"""

from __future__ import annotations

import logging
import os

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
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_auth_header"))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    request = await _apply(auth)
    assert request.headers["authorization"] == "Bearer static"


async def test_before_mcp_request_adds_tenant_header() -> None:
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_tenant_header"))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    request = await _apply(auth)
    assert request.headers["x-tenant"] == "acme"


async def test_hook_can_read_environment_and_construct_multiple_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_MCP_BEARER", "env-token")
    monkeypatch.setenv("INTERNAL_MCP_TENANT", "env-tenant")

    def add_env_auth(context, request):
        return request.with_headers(
            {
                "Authorization": f"Bearer {os.environ['INTERNAL_MCP_BEARER']}",
                "X-Tenant": os.environ["INTERNAL_MCP_TENANT"],
            }
        )

    import tests.runtime.hooks.fixtures as fx

    fx.add_env_auth = add_env_auth  # type: ignore[attr-defined]
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_env_auth"))
    auth = HookedMCPAuth(mgr, "internal-mcp")

    request = await _apply(auth)

    assert request.headers["authorization"] == "Bearer env-token"
    assert request.headers["x-tenant"] == "env-tenant"


async def test_base_auth_and_hooks_compose() -> None:
    # A plugin (base) auth runs first, hooks add on top.
    class _Base(httpx.Auth):
        async def async_auth_flow(self, request):
            request.headers["X-Base"] = "1"
            yield request

    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_tenant_header"))
    auth = HookedMCPAuth(mgr, "internal-mcp", base=_Base())
    request = await _apply(auth)
    assert request.headers["x-base"] == "1"
    assert request.headers["x-tenant"] == "acme"


async def test_hook_reads_current_run_context() -> None:
    def add_user(context, request):
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
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_auth_header"))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    with caplog.at_level(logging.DEBUG):
        await _apply(auth)
    assert "Bearer" not in caplog.text


# -- after_mcp_response (response seam: `response = yield request`) ----------


async def _drive_with_response(
    auth: httpx.Auth, status_code: int = 200
) -> tuple[httpx.Request, httpx.Response]:
    """Drive the auth flow through the full request→response cycle, exactly as
    httpx does: __anext__() to get the request, then asend(response)."""
    request = httpx.Request("POST", "https://internal.test/mcp")
    flow = auth.async_auth_flow(request)
    sent = await flow.__anext__()
    response = httpx.Response(status_code, request=sent)
    try:
        await flow.asend(response)
    except StopAsyncIteration:
        pass
    finally:
        await flow.aclose()
    return sent, response


async def test_after_mcp_response_runs_with_status_and_latency() -> None:
    from tests.runtime.hooks import fixtures

    fixtures.CALLS.clear()
    mgr = _manager(HookSpec("after_mcp_response", f"{_FIX}:record_after_mcp_response"))
    auth = HookedMCPAuth(mgr, "internal-mcp")

    await _drive_with_response(auth, status_code=503)

    resp = next(c[1] for c in fixtures.CALLS if c[0] == "after_mcp_response")
    assert resp.server_id == "internal-mcp"
    assert resp.status_code == 503
    assert resp.latency_ms is not None


async def test_after_mcp_response_noop_without_hooks_completes_flow() -> None:
    # No after_mcp_response hook: the response cycle still completes cleanly.
    mgr = _manager()
    auth = HookedMCPAuth(mgr, "internal-mcp")
    _, response = await _drive_with_response(auth, status_code=200)
    assert response.status_code == 200


async def test_after_mcp_response_does_not_log_body_or_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tests.runtime.hooks import fixtures

    fixtures.CALLS.clear()
    mgr = _manager(HookSpec("after_mcp_response", f"{_FIX}:record_after_mcp_response"))
    auth = HookedMCPAuth(mgr, "internal-mcp")
    with caplog.at_level(logging.DEBUG):
        await _drive_with_response(auth, status_code=200)
    # Only status/latency are logged — never header values or a body.
    assert "authorization" not in caplog.text.lower()
