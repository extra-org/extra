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

from agent_engine.runtime.hooks import HookInvocation, RunContext, ToolResultContext

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
