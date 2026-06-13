"""Critical lifecycle and routing logs for MCP and tool runtime stages."""

from __future__ import annotations

import asyncio
import logging

import pytest

from agent_engine.runtime.context import ExecutionContext
from agent_engine.runtime.mcp_manager import MCPManager
from agent_engine.runtime.tool_models import MCPToolDefinition
from agent_engine.runtime.tool_registry import (
    MCPToolProvider,
    ToolRegistry,
)
from agent_engine.spec.models import AgentEngineSpec, AgentSpec, McpSpec, SystemSpec


class FakeMCPClient:
    def __init__(self, tools: list[MCPToolDefinition]) -> None:
        self.tools = tools

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def list_tools(self) -> list[MCPToolDefinition]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        return {"tool": name, "arguments": arguments}


def _tool(server_id: str, name: str) -> MCPToolDefinition:
    return MCPToolDefinition(
        server_id=server_id,
        name=name,
        description=f"{name} description",
        parameters_schema={"type": "object"},
    )


def _spec() -> AgentEngineSpec:
    return AgentEngineSpec(
        system=SystemSpec(name="test-system"),
        graph={"search_agent": None},
        mcps={"deepwiki": McpSpec(url="https://example.com/deepwiki")},
        agents={
            "search_agent": AgentSpec(
                description="searches",
                mcps=["deepwiki"],
            )
        },
    )


def _manager() -> MCPManager:
    return MCPManager(
        {"deepwiki": McpSpec(url="https://example.com/deepwiki")},
        client_factory=lambda server_id, config: FakeMCPClient([_tool(server_id, "ask")]),
    )


def test_mcp_manager_logs_lifecycle(caplog: pytest.LogCaptureFixture) -> None:
    manager = _manager()

    with caplog.at_level(logging.INFO, logger="agent_engine.runtime.mcp_manager"):
        logging.getLogger("agent_engine").propagate = True
        asyncio.run(manager.start())
        asyncio.run(manager.stop())

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "MCPManager.start()" in messages
    assert "Connecting to MCP server=deepwiki" in messages
    assert "discovered tools=1" in messages
    assert "MCPManager.stop()" in messages


def test_mcp_manager_logs_tool_call(caplog: pytest.LogCaptureFixture) -> None:
    manager = _manager()
    ctx = ExecutionContext(message="hi", state={})

    async def scenario() -> None:
        await manager.start()
        try:
            await manager.call_tool(
                server_id="deepwiki",
                tool_name="ask",
                arguments={"question": "what"},
                ctx=ctx,
            )
        finally:
            await manager.stop()

    with caplog.at_level(logging.INFO, logger="agent_engine.runtime.mcp_manager"):
        logging.getLogger("agent_engine").propagate = True
        asyncio.run(scenario())

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Calling MCP tool=ask on server=deepwiki" in messages
    assert "succeeded" in messages


def test_mcp_manager_does_not_log_argument_values(caplog: pytest.LogCaptureFixture) -> None:
    manager = _manager()
    ctx = ExecutionContext(message="hi", state={})

    async def scenario() -> None:
        await manager.start()
        try:
            await manager.call_tool(
                server_id="deepwiki",
                tool_name="ask",
                arguments={"question": "super-secret-value"},
                ctx=ctx,
            )
        finally:
            await manager.stop()

    # Even at DEBUG, argument keys are logged but never values.
    with caplog.at_level(logging.DEBUG, logger="agent_engine.runtime.mcp_manager"):
        logging.getLogger("agent_engine").propagate = True
        asyncio.run(scenario())

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "super-secret-value" not in messages
    assert "argument keys=['question']" in messages


def test_tool_registry_logs_routing_decision(caplog: pytest.LogCaptureFixture) -> None:
    manager = _manager()
    registry = ToolRegistry(providers=[MCPToolProvider(spec=_spec(), mcp_manager=manager)])
    ctx = ExecutionContext(message="hi", state={})

    async def scenario() -> None:
        await manager.start()
        try:
            await registry.call_tool(
                agent_id="search_agent",
                tool_name="ask",
                arguments={"question": "what"},
                ctx=ctx,
            )
        finally:
            await manager.stop()

    with caplog.at_level(logging.INFO, logger="agent_engine.runtime.tool_registry"):
        logging.getLogger("agent_engine").propagate = True
        asyncio.run(scenario())

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Routing tool=ask for agent=search_agent provider=mcp server=deepwiki" in messages
