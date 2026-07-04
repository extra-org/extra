"""Behaviour tests for HookManager execution semantics."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agent_engine.core.spec import HooksConfig, HookSpec
from agent_engine.runtime.hooks.errors import HookExecutionError
from agent_engine.runtime.hooks.manager import HookManager
from agent_engine.runtime.hooks.models import (
    EngineContext,
    McpRequestContext,
    RunContext,
    ToolCallContext,
    ToolResultContext,
)
from tests.runtime.hooks import fixtures

_FIX = "tests.runtime.hooks.fixtures"


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    fixtures.CALLS.clear()


def _manager(*specs: HookSpec) -> HookManager:
    return HookManager.from_config(HooksConfig(hooks=specs))


def _manifest(tmp_path: Path) -> Path:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    manifest = plugins / "plugins.toml"
    manifest.write_text(
        '[hooks.plugins]\nmanaged = "tests.runtime.hooks.fixtures:ManagedHook"\n',
        encoding="utf-8",
    )
    return manifest


def test_from_config_none_returns_empty_manager() -> None:
    mgr = HookManager.from_config(None)
    assert mgr.hook_count == 0
    for point in (
        "on_engine_start",
        "on_run_start",
        "before_mcp_request",
        "after_tool_call",
        "on_run_error",
    ):
        assert mgr.has(point) is False


def test_from_empty_config_returns_empty_manager() -> None:
    mgr = HookManager.from_config(HooksConfig())
    assert mgr.hook_count == 0


def test_empty_config_with_manifest_path_does_not_read_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(path: Path) -> dict[str, str]:
        raise AssertionError(f"manifest should not be read: {path}")

    monkeypatch.setattr("agent_engine.generate.manifest.hook_plugin_refs", _fail_if_called)

    mgr = HookManager.from_config(HooksConfig(), manifest_path=tmp_path / "plugins.toml")

    assert mgr.hook_count == 0


async def test_empty_manager_hook_points_are_no_ops() -> None:
    mgr = HookManager.empty()
    run_context = RunContext(run_id="r1")
    request = McpRequestContext(server_id="s", url="https://x/mcp")

    await mgr.run_engine_start(EngineContext(system_name="s"))
    assert await mgr.run_run_start(run_context) is run_context
    assert await mgr.run_before_mcp_request(run_context, request) is request
    await mgr.run_after_tool_call(
        run_context, ToolCallContext(agent_id="a", tool_name="t", provider="local")
    )
    await mgr.run_run_error(run_context, RuntimeError("original"))
    assert fixtures.CALLS == []


async def test_executes_hooks_in_declaration_order() -> None:
    mgr = _manager(
        HookSpec("on_run_start", f"{_FIX}:sync_hook"),
        HookSpec("on_run_start", f"{_FIX}:async_hook"),
        HookSpec("on_run_start", f"{_FIX}:sync_hook"),
    )
    await mgr.run_run_start(RunContext())
    assert [c[0] for c in fixtures.CALLS] == ["sync", "async", "sync"]


async def test_supports_sync_and_async_hooks() -> None:
    mgr = _manager(
        HookSpec("on_run_start", f"{_FIX}:sync_hook"),
        HookSpec("on_run_start", f"{_FIX}:async_hook"),
    )
    await mgr.run_run_start(RunContext())
    assert {c[0] for c in fixtures.CALLS} == {"sync", "async"}


async def test_supports_callable_class_hook() -> None:
    mgr = _manager(HookSpec("on_run_start", f"{_FIX}:CallableHook"))
    await mgr.run_run_start(RunContext())
    assert fixtures.CALLS[0][0] == "callable"


async def test_class_method_hook_reuses_instance() -> None:
    fixtures.McpAuthHook.instances_created = 0
    mgr = _manager(
        HookSpec(
            "before_mcp_request",
            f"{_FIX}:McpAuthHook.before_mcp_request",
        )
    )

    first = await mgr.run_before_mcp_request(
        RunContext(run_id="r1"), McpRequestContext(server_id="s", url="https://x/mcp")
    )
    second = await mgr.run_before_mcp_request(
        RunContext(run_id="r2"), McpRequestContext(server_id="s", url="https://x/mcp")
    )

    assert first.headers["X-Audience"] == "internal-docs"
    assert second.headers["X-Audience"] == "internal-docs"
    assert fixtures.McpAuthHook.instances_created == 1
    assert fixtures.CALLS[0][0] == "mcp_auth_method"
    assert fixtures.CALLS[1][0] == "mcp_auth_method"
    assert fixtures.CALLS[0][1] == fixtures.CALLS[1][1]
    assert [call[2] for call in fixtures.CALLS] == [1, 2]
    assert fixtures.CALLS[0][3].run_id == "r1"


async def test_plugin_method_hook_receives_invocation(tmp_path: Path) -> None:
    fixtures.ManagedHook.instances_created = 0
    mgr = HookManager.from_config(
        HooksConfig(
            hooks=(
                HookSpec(
                    "before_mcp_request",
                    plugin="managed",
                    method="before_mcp_request",
                ),
            )
        ),
        manifest_path=_manifest(tmp_path),
    )

    request = await mgr.run_before_mcp_request(
        RunContext(run_id="r1"), McpRequestContext(server_id="s", url="https://x/mcp")
    )

    assert request.headers["Authorization"] == "Bearer static"
    assert request.headers["X-Tenant"] == "acme"
    assert fixtures.ManagedHook.instances_created == 1
    event = fixtures.CALLS[0][3]
    assert event.plugin == "managed"
    assert event.method == "before_mcp_request"
    assert isinstance(event.payload, McpRequestContext)
    assert event.run_context.run_id == "r1"


async def test_explicit_ref_hook_does_not_read_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(path: Path) -> dict[str, str]:
        raise AssertionError(f"manifest should not be read: {path}")

    monkeypatch.setattr("agent_engine.generate.manifest.hook_plugin_refs", _fail_if_called)

    mgr = HookManager.from_config(
        HooksConfig(
            hooks=(
                HookSpec(
                    "on_run_start",
                    f"{_FIX}:run_start_enrich",
                ),
            )
        ),
        manifest_path=tmp_path / "missing-plugins.toml",
    )

    result = await mgr.run_run_start(RunContext(run_id="r1"))

    assert result.user_id == "alice"


async def test_plugin_instance_reused_across_hook_points_and_executions(tmp_path: Path) -> None:
    fixtures.ManagedHook.instances_created = 0
    mgr = HookManager.from_config(
        HooksConfig(
            hooks=(
                HookSpec(
                    "on_run_start",
                    plugin="managed",
                    method="attach_user_context",
                ),
                HookSpec(
                    "before_mcp_request",
                    plugin="managed",
                    method="before_mcp_request",
                ),
            )
        ),
        manifest_path=_manifest(tmp_path),
    )

    updated = await mgr.run_run_start(RunContext(run_id="r1"))
    first = await mgr.run_before_mcp_request(
        updated, McpRequestContext(server_id="s", url="https://x/mcp")
    )
    second = await mgr.run_before_mcp_request(
        updated, McpRequestContext(server_id="s", url="https://x/mcp")
    )

    assert updated.user_id == "managed-user"
    assert first.headers == {"Authorization": "Bearer static", "X-Tenant": "acme"}
    assert second.headers == {"Authorization": "Bearer static", "X-Tenant": "acme"}
    assert fixtures.ManagedHook.instances_created == 1
    instance_ids = [call[1] for call in fixtures.CALLS]
    assert instance_ids == [instance_ids[0], instance_ids[0], instance_ids[0]]
    assert [call[2] for call in fixtures.CALLS] == [1, 2, 3]


def test_missing_hook_plugin_id_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(Exception) as exc:
        HookManager.from_config(
            HooksConfig(
                hooks=(
                    HookSpec(
                        "before_mcp_request",
                        plugin="missing",
                        method="before_mcp_request",
                    ),
                )
            ),
            manifest_path=_manifest(tmp_path),
        )
    assert "not declared in [hooks.plugins]" in str(exc.value)


def test_plugin_method_hook_with_missing_manifest_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(Exception) as exc:
        HookManager.from_config(
            HooksConfig(
                hooks=(
                    HookSpec(
                        "before_mcp_request",
                        plugin="managed",
                        method="before_mcp_request",
                    ),
                )
            ),
            manifest_path=tmp_path / "plugins.toml",
        )

    assert "not declared in [hooks.plugins]" in str(exc.value)


async def test_runs_hook_without_yaml_config() -> None:
    mgr = _manager(HookSpec("on_engine_start", f"{_FIX}:sync_hook"))
    await mgr.run_engine_start(EngineContext(system_name="s"))
    assert fixtures.CALLS[0][0] == "sync"


async def test_run_start_returns_updated_context() -> None:
    mgr = _manager(HookSpec("on_run_start", f"{_FIX}:run_start_enrich"))
    result = await mgr.run_run_start(RunContext(run_id="r1"))
    assert result.user_id == "alice"
    assert result.run_id == "r1"


async def test_before_mcp_request_returns_updated_request() -> None:
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_auth_header"))
    req = await mgr.run_before_mcp_request(
        RunContext(), McpRequestContext(server_id="s", url="https://x/mcp")
    )
    assert req.headers["Authorization"] == "Bearer static"


async def test_hook_failure_raises_with_point_and_ref() -> None:
    mgr = _manager(HookSpec("after_tool_call", f"{_FIX}:boom"))
    with pytest.raises(HookExecutionError) as exc:
        await mgr.run_after_tool_call(
            RunContext(), ToolCallContext(agent_id="a", tool_name="t", provider="local")
        )
    assert exc.value.point == "after_tool_call"
    assert exc.value.ref.endswith("boom")
    assert isinstance(exc.value.cause, RuntimeError)


async def test_failure_policy_warn_does_not_raise() -> None:
    mgr = _manager(HookSpec("after_tool_call", f"{_FIX}:boom", failure_policy="warn"))
    # Should swallow the error and continue.
    await mgr.run_after_tool_call(
        RunContext(), ToolCallContext(agent_id="a", tool_name="t", provider="local")
    )


async def test_managed_hook_failure_policy_warn_does_not_raise(tmp_path: Path) -> None:
    mgr = HookManager.from_config(
        HooksConfig(
            hooks=(
                HookSpec(
                    "after_tool_call",
                    plugin="managed",
                    method="audit_warn",
                    failure_policy="warn",
                ),
            )
        ),
        manifest_path=_manifest(tmp_path),
    )

    await mgr.run_after_tool_call(
        RunContext(), ToolCallContext(agent_id="a", tool_name="t", provider="local")
    )
    assert fixtures.CALLS[0][0] == "managed_warn"


async def test_run_error_hook_failure_is_swallowed() -> None:
    mgr = _manager(HookSpec("on_run_error", f"{_FIX}:boom"))
    # run_run_error must never raise — the original error is what matters.
    await mgr.run_run_error(RunContext(), ValueError("original"))


async def test_logs_do_not_leak_hook_payload_values(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager(HookSpec("on_engine_start", f"{_FIX}:sync_hook"))
    with caplog.at_level(logging.DEBUG, logger="agent_engine.runtime.hooks.manager"):
        await mgr.run_engine_start(EngineContext(system_name="SECRET_VALUE_XYZ"))
    text = caplog.text
    assert "SECRET_VALUE_XYZ" not in text


def test_has_reports_declared_points() -> None:
    mgr = _manager(HookSpec("before_mcp_request", f"{_FIX}:add_auth_header"))
    assert mgr.has("before_mcp_request") is True
    assert mgr.has("after_tool_call") is False


# -- lifecycle coverage ------------------------------------------------------


async def test_transform_tool_result_returns_modified_result() -> None:
    mgr = _manager(HookSpec("transform_tool_result", f"{_FIX}:truncate_tool_result"))
    out = await mgr.run_transform_tool_result(
        None, ToolResultContext("a", "t", "mcp", result="abcdefgh")
    )
    assert out.result == "abc"  # truncated to the configured limit


async def test_transform_tool_result_warn_failure_keeps_original() -> None:
    # A failing transform under failure_policy=warn must not alter the result.
    mgr = _manager(HookSpec("transform_tool_result", f"{_FIX}:boom", failure_policy="warn"))
    original = ToolResultContext("a", "t", "mcp", result="keep-me")
    out = await mgr.run_transform_tool_result(None, original)
    assert out is original


_RUN_METHOD = {
    "on_engine_start": "run_engine_start",
    "on_engine_stop": "run_engine_stop",
    "on_run_start": "run_run_start",
    "on_run_end": "run_run_end",
    "on_run_error": "run_run_error",
    "before_tool_call": "run_before_tool_call",
    "after_tool_call": "run_after_tool_call",
    "transform_tool_result": "run_transform_tool_result",
    "on_tool_error": "run_on_tool_error",
    "before_mcp_request": "run_before_mcp_request",
    "after_mcp_response": "run_after_mcp_response",
}


def test_every_hook_point_has_a_run_method() -> None:
    from agent_engine.runtime.hooks.models import HOOK_POINTS

    # The map is exhaustive over HOOK_POINTS and each method exists.
    assert set(_RUN_METHOD) == set(HOOK_POINTS)
    mgr = HookManager.empty()
    for method in _RUN_METHOD.values():
        assert callable(getattr(mgr, method))


async def test_empty_manager_no_ops_for_every_point() -> None:
    from agent_engine.runtime.hooks.models import (
        EngineContext,
        McpRequestContext,
        McpResponseContext,
        RunEndContext,
        ToolCallContext,
        ToolRequestContext,
    )

    mgr = HookManager.empty()
    assert mgr.hook_count == 0
    ctx = RunContext()
    # None of these should raise or have side effects.
    await mgr.run_engine_start(EngineContext(system_name="s"))
    await mgr.run_engine_stop(EngineContext(system_name="s"))
    assert await mgr.run_run_start(ctx) is ctx  # unchanged
    await mgr.run_run_end(ctx, RunEndContext())
    await mgr.run_run_error(ctx, RuntimeError("x"))
    await mgr.run_before_tool_call(ctx, ToolRequestContext("a", "t", "local"))
    await mgr.run_after_tool_call(ctx, ToolCallContext("a", "t", "local"))
    await mgr.run_on_tool_error(ctx, ToolCallContext("a", "t", "local", status="failed"))
    req = McpRequestContext(server_id="s", url="https://x/mcp")
    assert await mgr.run_before_mcp_request(ctx, req) is req  # unchanged
    await mgr.run_after_mcp_response(ctx, McpResponseContext("s", "https://x/mcp", 200))
