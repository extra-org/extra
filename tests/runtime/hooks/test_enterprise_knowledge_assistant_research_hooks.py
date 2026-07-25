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


async def test_research_hook_truncates_large_deepwiki_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    raw_result = "DeepWiki body " + ("X" * 9000)
    event = HookInvocation(
        hook_point="transform_tool_result",
        payload=ToolResultContext(
            agent_id="repository_agent",
            tool_name="ask_question",
            provider="mcp",
            server_id="deepwiki",
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
    assert "DeepWiki body" not in caplog.text
    assert "XXXXX" not in caplog.text


async def test_research_hook_leaves_non_deepwiki_results_unchanged() -> None:
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    result = "Z" * 9000

    cases: tuple[tuple[Literal["mcp", "local"], str | None], ...] = (
        ("mcp", "context7"),
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


async def test_context7_auth_logs_only_when_header_is_attached(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_research_hooks()
    hook = module.ResearchHooksHook()
    api_key = "secret-context7-key"
    monkeypatch.setenv("CONTEXT7_API_KEY", api_key)
    manager = HookManager(
        {
            "before_mcp_request": [
                LoadedHook(
                    point="before_mcp_request",
                    ref="research_hooks:inject_context7_auth",
                    func=hook.inject_context7_auth,
                    plugin="research_hooks",
                    method="inject_context7_auth",
                    event_mode=True,
                )
            ]
        }
    )
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="agent_engine.runtime.hooks.manager"):
        deepwiki = McpRequestContext(server_id="deepwiki", url="https://deepwiki.test/mcp")
        unchanged = await manager.run_before_mcp_request(None, deepwiki)

    assert unchanged is deepwiki
    assert caplog.text == ""

    with caplog.at_level(logging.INFO, logger="agent_engine.runtime.hooks.manager"):
        context7 = McpRequestContext(server_id="context7", url="https://context7.test/mcp")
        updated = await manager.run_before_mcp_request(None, context7)

    assert updated.headers["CONTEXT7_API_KEY"] == api_key
    assert (
        "hook applied point=before_mcp_request ref=research_hooks:inject_context7_auth"
        in caplog.text
    )
    assert api_key not in caplog.text
