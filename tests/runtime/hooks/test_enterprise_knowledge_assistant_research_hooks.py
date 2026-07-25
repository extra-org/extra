"""Behavior tests for the enterprise-knowledge-assistant example hook plugin.

The directory contains a hyphen, so import the plugin by file path instead
of relying on normal package imports.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import ModuleType
from typing import Literal

import pytest

from agent_engine.runtime.hooks import (
    HookInvocation,
    McpRequestContext,
    RunContext,
    ToolResultContext,
)
from agent_engine.runtime.hooks.manager import HookManager, LoadedHook

REPO_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_HOOKS = (
    REPO_ROOT
    / "examples"
    / "enterprise-knowledge-assistant"
    / "plugins"
    / "hooks"
    / "research_hooks.py"
)


def _load_research_hooks() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "enterprise_knowledge_assistant_research_hooks", RESEARCH_HOOKS
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_research_hook_truncates_large_knowledge_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    raw_result = "Knowledge body " + ("X" * 9000)
    event = HookInvocation(
        hook_point="transform_tool_result",
        payload=ToolResultContext(
            agent_id="repository_agent",
            tool_name="search_internal_documents",
            provider="mcp",
            server_id="local_knowledge_mcp",
            result=raw_result,
        ),
        run_context=RunContext(run_id="run-1"),
    )

    with caplog.at_level(logging.INFO, logger="research_hooks"):
        transformed = await hook.truncate_tool_result(event)

    assert transformed.result.startswith(raw_result[:8000])
    assert len(transformed.result) < len(raw_result)
    assert "[truncated to 8000" in transformed.result
    assert "original_chars=" in caplog.text
    assert "kept_chars=8000" in caplog.text
    assert "Knowledge body" not in caplog.text
    assert "XXXXX" not in caplog.text


async def test_research_hook_leaves_other_results_unchanged() -> None:
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    result = "Z" * 9000

    cases: tuple[tuple[Literal["mcp", "local"], str | None], ...] = (
        ("mcp", "some_other_mcp"),
        ("local", None),
    )
    for provider, server_id in cases:
        original = ToolResultContext(
            agent_id="repository_agent",
            tool_name="tool",
            provider=provider,
            server_id=server_id,
            result=result,
        )
        event = HookInvocation(hook_point="transform_tool_result", payload=original)

        transformed = await hook.truncate_tool_result(event)

        assert transformed is original


async def test_mcp_auth_logs_only_when_header_is_attached(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    credential = "secret-local-mcp-token"
    monkeypatch.setenv("LOCAL_MCP_TOKEN", credential)
    manager = HookManager(
        {
            "before_mcp_request": [
                LoadedHook(
                    point="before_mcp_request",
                    ref="research_hooks:inject_mcp_auth",
                    func=hook.inject_mcp_auth,
                    plugin="research_hooks",
                    method="inject_mcp_auth",
                    event_mode=True,
                )
            ]
        }
    )
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="agent_engine.runtime.hooks.manager"):
        public = McpRequestContext(server_id="public_mcp", url="https://public.test/mcp")
        unchanged = await manager.run_before_mcp_request(None, public)

    assert unchanged is public
    assert caplog.text == ""

    with caplog.at_level(logging.INFO, logger="agent_engine.runtime.hooks.manager"):
        knowledge = McpRequestContext(
            server_id="local_knowledge_mcp", url="http://127.0.0.1:8765/mcp"
        )
        updated = await manager.run_before_mcp_request(None, knowledge)

    assert updated.headers["Authorization"] == credential
    assert "hook applied point=before_mcp_request ref=research_hooks:inject_mcp_auth" in caplog.text
    assert credential not in caplog.text


async def test_mcp_auth_passes_through_when_credential_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local knowledge server needs no credential, so an unset token is not
    an error — the request goes out unauthenticated."""
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    monkeypatch.delenv("LOCAL_MCP_TOKEN", raising=False)

    request = McpRequestContext(server_id="local_knowledge_mcp", url="http://127.0.0.1:8765/mcp")
    event = HookInvocation(hook_point="before_mcp_request", payload=request)

    assert await hook.inject_mcp_auth(event) is request
