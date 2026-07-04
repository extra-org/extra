"""Behaviour tests for HookLoader — importing hook refs by path."""

from __future__ import annotations

import pytest

from agent_engine.runtime.hooks.errors import HookLoadError
from agent_engine.runtime.hooks.loader import HookLoader
from agent_engine.runtime.hooks.models import HookInvocation, McpRequestContext, RunContext

_FIX = "tests.runtime.hooks.fixtures"


def test_loads_function_by_colon_ref() -> None:
    fn = HookLoader().load("on_run_start", f"{_FIX}:sync_hook")
    assert callable(fn)


def test_loads_function_by_dotted_ref() -> None:
    fn = HookLoader().load("on_run_start", f"{_FIX}.sync_hook")
    assert callable(fn)


def test_loads_class_hook_as_instance() -> None:
    hook = HookLoader().load("on_run_start", f"{_FIX}:CallableHook")
    # A class ref is instantiated; the returned object must itself be callable.
    assert callable(hook)
    assert hook.__class__.__name__ == "CallableHook"


def test_loads_class_method_hook_with_unconfigured_instance() -> None:
    hook = HookLoader().load(
        "before_mcp_request",
        f"{_FIX}:McpAuthHook.before_mcp_request",
    )
    assert callable(hook)
    instance = hook.__agent_hook_instance__  # type: ignore[attr-defined]
    assert instance.__class__.__name__ == "McpAuthHook"


async def test_class_method_hook_callable_can_be_invoked() -> None:
    hook = HookLoader().load(
        "before_mcp_request",
        f"{_FIX}:McpAuthHook.before_mcp_request",
    )
    request = await hook(RunContext(), McpRequestContext(server_id="s", url="https://x/mcp"))
    assert request.headers["X-Audience"] == "internal-docs"


async def test_loads_managed_plugin_method_and_reuses_instance() -> None:
    instances: dict[str, object] = {}
    loader = HookLoader()
    hook = loader.load_plugin_method(
        "before_mcp_request",
        "managed",
        "before_mcp_request",
        f"{_FIX}:ManagedHook",
        instances,
    )
    second = loader.load_plugin_method(
        "on_run_start",
        "managed",
        "attach_user_context",
        f"{_FIX}:ManagedHook",
        instances,
    )

    request = await hook(
        HookInvocation(
            hook_point="before_mcp_request",
            plugin="managed",
            method="before_mcp_request",
            run_context=RunContext(run_id="r1"),
            payload=McpRequestContext(server_id="s", url="https://x/mcp"),
        )
    )

    assert request.headers["Authorization"] == "Bearer static"
    assert request.headers["X-Tenant"] == "acme"
    assert hook.__agent_hook_instance__ is second.__agent_hook_instance__  # type: ignore[attr-defined]


def test_missing_module_fails_clearly() -> None:
    with pytest.raises(HookLoadError) as exc:
        HookLoader().load("on_run_start", "no.such.module:thing")
    assert exc.value.point == "on_run_start"
    assert exc.value.ref == "no.such.module:thing"
    assert "cannot import" in str(exc.value)


def test_missing_attribute_fails_clearly() -> None:
    with pytest.raises(HookLoadError) as exc:
        HookLoader().load("on_run_start", f"{_FIX}:does_not_exist")
    assert "no attribute" in str(exc.value)


def test_missing_class_method_fails_clearly() -> None:
    with pytest.raises(HookLoadError) as exc:
        HookLoader().load("before_mcp_request", f"{_FIX}:McpAuthHook.nope")
    assert "no method" in str(exc.value)


def test_non_callable_class_method_fails_clearly() -> None:
    with pytest.raises(HookLoadError) as exc:
        HookLoader().load("before_mcp_request", f"{_FIX}:NonCallableMethodHook.before_mcp_request")
    assert "not callable" in str(exc.value)


def test_invalid_constructor_fails_clearly() -> None:
    with pytest.raises(HookLoadError) as exc:
        HookLoader().load(
            "before_mcp_request",
            f"{_FIX}:MissingConfigConstructorHook.before_mcp_request",
        )
    assert "invalid constructor" in str(exc.value)


def test_non_callable_target_fails_clearly() -> None:
    with pytest.raises(HookLoadError) as exc:
        HookLoader().load("on_run_start", f"{_FIX}:not_callable")
    assert "not callable" in str(exc.value)


def test_ref_without_module_or_attr_fails() -> None:
    with pytest.raises(HookLoadError):
        HookLoader().load("on_run_start", "justname")
