"""Regression tests for async and sync local tool execution in the runtime engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.tool import ToolCall as LCToolCall

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    ModelConfig,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


class SingleToolCallModel:
    """Fake chat model that invokes a specified tool once and inspects returned messages."""

    def __init__(
        self,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args or {"message": "reviewed-value"}
        self.calls = 0
        self.received_tool_messages: list[ToolMessage] = []

    def bind_tools(self, tools: list[Any]) -> SingleToolCallModel:
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        for m in messages:
            if isinstance(m, ToolMessage):
                self.received_tool_messages.append(m)
                return AIMessage(content=f"model-received: {m.content}")

        self.calls += 1
        return AIMessage(
            content="",
            tool_calls=[
                LCToolCall(
                    name=self.tool_name,
                    args=self.tool_args,
                    id=f"call-{self.calls}",
                )
            ],
        )


def _spec(tool_id: str, *, auto_mode: bool = True) -> SystemSpec:
    agent = AgentSpec(
        id="tool_agent",
        name="tool_agent",
        description="test tool agent",
        model=_MODEL,
        prompts=BasePromptSet(),
        tools=(ToolSpec(tool_id, f"Tool {tool_id}"),),
        auto_mode=auto_mode,
    )
    return SystemSpec(
        meta=SystemMeta(name="async_tool_test"),
        defaults=None,
        graph=GraphNode(node=agent),
    )


def _write_tool(base_dir: Path, tool_id: str, code: str) -> None:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / f"{tool_id}.py").write_text(code, encoding="utf-8")


@pytest.mark.asyncio
async def test_async_local_tool_executes_once_and_returns_value_to_model(
    tmp_path: Path,
) -> None:
    counter_file = tmp_path / "calls.txt"
    _write_tool(
        tmp_path,
        "record_value",
        "from pathlib import Path\n"
        "async def record_value(message: str) -> str:\n"
        f"    with open({str(counter_file)!r}, 'a') as f:\n"
        "        f.write('x')\n"
        "    return f'processed-{message}'\n",
    )
    model = SingleToolCallModel("record_value", {"message": "reviewed-value"})
    async with LangGraphEngine(
        tmp_path, model_factory=lambda *a, **kw: cast(BaseChatModel, model)
    ) as engine:
        await engine.build(_spec("record_value"))
        result = await engine.run("run tool")

    # 1. The async tool executes exactly once
    assert counter_file.exists()
    assert counter_file.read_text() == "x"

    # 2. The resolved return value reaches the model as ToolMessage content (not coroutine object)
    assert len(model.received_tool_messages) == 1
    tool_msg = model.received_tool_messages[0]
    assert tool_msg.content == "processed-reviewed-value"
    assert "coroutine" not in str(tool_msg.content).lower()

    # Model answer reflects tool output and tool usage status is succeeded
    assert result.answer == "model-received: processed-reviewed-value"
    assert len(result.used_tools) == 1
    assert result.used_tools[0].status == "succeeded"


@pytest.mark.asyncio
async def test_async_local_tool_exception_recorded_as_failed(
    tmp_path: Path,
) -> None:
    _write_tool(
        tmp_path,
        "failing_tool",
        "async def failing_tool(message: str) -> str:\n"
        "    raise ValueError(f'async failure: {message}')\n",
    )
    model = SingleToolCallModel("failing_tool", {"message": "bad-arg"})
    async with LangGraphEngine(
        tmp_path, model_factory=lambda *a, **kw: cast(BaseChatModel, model)
    ) as engine:
        await engine.build(_spec("failing_tool"))
        result = await engine.run("run failing tool")

    # 3. An exception raised inside an async local tool is recorded as a failed tool execution
    assert len(model.received_tool_messages) == 1
    tool_msg = model.received_tool_messages[0]
    assert "Tool error: async failure: bad-arg" in str(tool_msg.content)

    assert len(result.used_tools) == 1
    assert result.used_tools[0].status == "failed"
    assert "async failure: bad-arg" in (result.used_tools[0].error or "")


@pytest.mark.asyncio
async def test_async_local_tool_cancellation_propagates(
    tmp_path: Path,
) -> None:
    started_file = tmp_path / "started.txt"
    cancelled_file = tmp_path / "cancelled.txt"

    _write_tool(
        tmp_path,
        "cancelling_tool",
        "import asyncio\n"
        "from pathlib import Path\n"
        "async def cancelling_tool(message: str) -> str:\n"
        f"    Path({str(started_file)!r}).write_text('started')\n"
        "    try:\n"
        "        await asyncio.sleep(10)\n"
        "    except asyncio.CancelledError:\n"
        f"        Path({str(cancelled_file)!r}).write_text('cancelled')\n"
        "        raise\n"
        "    return 'done'\n",
    )

    model = SingleToolCallModel("cancelling_tool", {"message": "test"})
    async with LangGraphEngine(
        tmp_path, model_factory=lambda *a, **kw: cast(BaseChatModel, model)
    ) as engine:
        await engine.build(_spec("cancelling_tool"))
        task = asyncio.create_task(engine.run("run tool"))

        # Wait until the tool actually starts execution
        for _ in range(50):
            if started_file.exists():
                break
            await asyncio.sleep(0.05)

        assert started_file.exists()

        # Cancel the execution task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # 4. Cancellation propagates to the async local tool
    assert cancelled_file.exists()
    assert cancelled_file.read_text() == "cancelled"


@pytest.mark.asyncio
async def test_sync_local_tool_follows_existing_execution_path(
    tmp_path: Path,
) -> None:
    counter_file = tmp_path / "sync_calls.txt"
    _write_tool(
        tmp_path,
        "sync_tool",
        "from pathlib import Path\n"
        "def sync_tool(message: str) -> str:\n"
        f"    with open({str(counter_file)!r}, 'a') as f:\n"
        "        f.write('s')\n"
        "    return f'sync-{message}'\n",
    )
    model = SingleToolCallModel("sync_tool", {"message": "reviewed-value"})
    async with LangGraphEngine(
        tmp_path, model_factory=lambda *a, **kw: cast(BaseChatModel, model)
    ) as engine:
        await engine.build(_spec("sync_tool"))
        result = await engine.run("run sync tool")

    # 5. Synchronous local tools still follow existing execution path
    assert counter_file.exists()
    assert counter_file.read_text() == "s"

    assert len(model.received_tool_messages) == 1
    tool_msg = model.received_tool_messages[0]
    assert tool_msg.content == "sync-reviewed-value"

    assert result.answer == "model-received: sync-reviewed-value"
    assert len(result.used_tools) == 1
    assert result.used_tools[0].status == "succeeded"
