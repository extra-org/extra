"""Tests for tool result normalization and MCP structuredContent support."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.messages import ToolMessage

from agent_engine.approvals.in_memory_tool_execution_repository import (
    InMemoryToolExecutionRepository,
)
from agent_engine.approvals.tool_execution_manager import ToolExecutionManager
from agent_engine.engine.langgraph.tools.tool_result import (
    normalize_tool_result,
)
from agent_engine.runtime.hooks.models import ToolResultContext


def test_normalize_plain_string_tool() -> None:
    res = normalize_tool_result("hello world")
    assert res.text == "hello world"
    assert res.structured is None
    assert res.artifact is None


def test_normalize_mcp_text_and_structured_content() -> None:
    msg = ToolMessage(
        content=[{"type": "text", "text": "Found 2 invoices"}],
        tool_call_id="call_123",
        artifact={
            "structuredContent": {
                "invoices": [
                    {"id": "INV-123", "amount": 500},
                    {"id": "INV-456", "amount": 800},
                ]
            }
        },
    )
    res = normalize_tool_result(msg)
    assert res.text == "Found 2 invoices"
    assert res.structured == {
        "invoices": [
            {"id": "INV-123", "amount": 500},
            {"id": "INV-456", "amount": 800},
        ]
    }
    assert res.artifact == {
        "structuredContent": {
            "invoices": [
                {"id": "INV-123", "amount": 500},
                {"id": "INV-456", "amount": 800},
            ]
        }
    }


def test_normalize_multiple_text_blocks_and_structured_content() -> None:
    msg = ToolMessage(
        content=[
            {"type": "text", "text": "Header line.\n"},
            {"type": "text", "text": "Detail line."},
        ],
        tool_call_id="call_456",
        artifact={"structuredContent": {"count": 42}},
    )
    res = normalize_tool_result(msg)
    assert res.text == "Header line.\nDetail line."
    assert res.structured == {"count": 42}


def test_normalize_structured_only_result() -> None:
    data = {"structuredContent": {"balance": 1250}}
    res = normalize_tool_result(data)
    assert res.structured == {"balance": 1250}
    assert "1250" in res.text


def test_normalize_local_structured_dict() -> None:
    local_data = {"status": "ok", "items": [1, 2, 3]}
    res = normalize_tool_result(local_data)
    assert res.structured == {"status": "ok", "items": [1, 2, 3]}
    assert '{"status": "ok", "items": [1, 2, 3]}' in res.text


@dataclass
class CustomToolOutput:
    status: str
    count: int


def test_normalize_local_dataclass() -> None:
    output = CustomToolOutput(status="success", count=5)
    res = normalize_tool_result(output)
    assert res.structured == {"status": "success", "count": 5}
    assert "success" in res.text


@pytest.mark.asyncio
async def test_execution_manager_persists_and_restores_structured_and_artifact() -> None:
    repo = InMemoryToolExecutionRepository()
    manager = ToolExecutionManager(execution_repository=repo)

    exec_id = "exec_test_123"
    await manager.begin_execution(
        exec_id, tool_call_id="tc_1", run_id="run_1", tool_name="test_tool"
    )
    await manager.finish_execution(
        exec_id,
        status="succeeded",
        result="Found items",
        structured={"items": ["a", "b"]},
        artifact={"meta": "v1"},
    )

    record = await manager.already_executed(exec_id)
    assert record is not None
    assert record.result == "Found items"
    assert record.structured == {"items": ["a", "b"]}
    assert record.artifact == {"meta": "v1"}


def test_normalize_structured_only_tool_message() -> None:
    msg = ToolMessage(
        content=[],
        tool_call_id="call_structured_only",
        artifact={"structuredContent": {"balance": 1250}},
    )
    res = normalize_tool_result(msg)
    assert res.structured == {"balance": 1250}
    assert res.text == '{"balance": 1250}'


def test_normalize_auxiliary_artifact_not_conflated_with_structured() -> None:
    msg = ToolMessage(
        content="Generated report PDF",
        tool_call_id="call_aux_artifact",
        artifact={"file_id": "abc_123", "mime_type": "application/pdf"},
    )
    res = normalize_tool_result(msg)
    assert res.text == "Generated report PDF"
    assert res.structured is None
    assert res.artifact == {"file_id": "abc_123", "mime_type": "application/pdf"}


def test_tool_result_context_with_result_preserves_and_clears_structured() -> None:
    ctx = ToolResultContext(
        agent_id="a1",
        tool_name="t1",
        provider="mcp",
        result="Long raw output text",
        structured={"data": [1, 2, 3]},
        artifact={"meta": "v1"},
    )

    # Truncate text while preserving structured and artifact
    updated = ctx.with_result("Truncated text")
    assert updated.result == "Truncated text"
    assert updated.structured == {"data": [1, 2, 3]}
    assert updated.artifact == {"meta": "v1"}

    # Explicitly clear structured and artifact
    cleared = ctx.with_result("Cleared text", structured=None, artifact=None)
    assert cleared.result == "Cleared text"
    assert cleared.structured is None
    assert cleared.artifact is None
