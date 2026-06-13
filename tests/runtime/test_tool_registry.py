"""ToolRegistry boundary behavior across local and MCP providers."""

from __future__ import annotations

import pytest

from agent_engine.runtime.context import ExecutionContext
from agent_engine.runtime.mcp_manager import MCPManager
from agent_engine.runtime.tool_models import MCPToolDefinition
from agent_engine.runtime.tool_registry import (
    LocalToolProvider,
    MCPToolProvider,
    ToolRegistry,
    ToolRegistryError,
)
from agent_engine.spec.models import AgentEngineSpec, AgentSpec, McpSpec, SystemSpec, ToolSpec


class FakeMCPClient:
    def __init__(self, tools: list[MCPToolDefinition]) -> None:
        self.tools = tools
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def list_tools(self) -> list[MCPToolDefinition]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        return {"tool": name, "arguments": arguments}


def _tool(server_id: str, name: str) -> MCPToolDefinition:
    return MCPToolDefinition(
        server_id=server_id,
        name=name,
        description=f"{name} description",
        parameters_schema={"type": "object"},
    )


def _spec(
    *,
    domestic_tools: list[str] | None = None,
    domestic_mcps: list[str] | None = None,
    super_tools: list[str] | None = None,
    super_mcps: list[str] | None = None,
    tools: dict[str, ToolSpec] | None = None,
) -> AgentEngineSpec:
    return AgentEngineSpec(
        system=SystemSpec(name="test-system"),
        graph={"domestic_flights_agent": None, "super_agent": None, "empty_agent": None},
        mcps={
            "flights_mcp": McpSpec(url="https://example.com/flights-mcp"),
            "admin_mcp": McpSpec(url="https://example.com/admin-mcp"),
        },
        tools=tools
        or {
            "book_flight": ToolSpec(description="Book a flight"),
            "add_to_cart": ToolSpec(description="Add item to cart"),
        },
        agents={
            "domestic_flights_agent": AgentSpec(
                description="Domestic flights",
                tools=domestic_tools or [],
                mcps=domestic_mcps or [],
            ),
            "super_agent": AgentSpec(
                description="Supermarket",
                tools=super_tools or [],
                mcps=super_mcps or [],
            ),
            "empty_agent": AgentSpec(description="Empty"),
        },
    )


def _registry(
    spec: AgentEngineSpec,
    clients: dict[str, FakeMCPClient],
) -> ToolRegistry:
    def factory(server_id: str, config: McpSpec) -> FakeMCPClient:
        return clients[server_id]

    manager = MCPManager(spec.mcps, client_factory=factory)
    return ToolRegistry(
        providers=[
            LocalToolProvider(spec=spec),
            MCPToolProvider(spec=spec, mcp_manager=manager),
        ]
    )


async def _started_registry(
    spec: AgentEngineSpec,
    clients: dict[str, FakeMCPClient],
) -> ToolRegistry:
    def factory(server_id: str, config: McpSpec) -> FakeMCPClient:
        return clients[server_id]

    manager = MCPManager(spec.mcps, client_factory=factory)
    await manager.start()
    return ToolRegistry(
        providers=[
            LocalToolProvider(spec=spec),
            MCPToolProvider(spec=spec, mcp_manager=manager),
        ]
    )


def test_agent_gets_only_declared_local_tools() -> None:
    spec = _spec(domestic_tools=["book_flight"], super_tools=["add_to_cart"])
    registry = _registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([]),
            "admin_mcp": FakeMCPClient([]),
        },
    )

    tools = registry.get_tools_for_agent("domestic_flights_agent")

    assert [tool.name for tool in tools] == ["book_flight"]
    assert "add_to_cart" not in {tool.name for tool in tools}


async def test_agent_gets_only_mcp_tools_from_declared_servers() -> None:
    spec = _spec(domestic_mcps=["flights_mcp"], super_mcps=["admin_mcp"])
    registry = await _started_registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([_tool("flights_mcp", "flights_search")]),
            "admin_mcp": FakeMCPClient([_tool("admin_mcp", "admin_action")]),
        },
    )

    tools = registry.get_tools_for_agent("domestic_flights_agent")

    assert [tool.name for tool in tools] == ["flights_search"]
    assert "admin_action" not in {tool.name for tool in tools}


def test_agent_without_tools_or_mcps_gets_empty_list() -> None:
    spec = _spec()
    registry = _registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([]),
            "admin_mcp": FakeMCPClient([]),
        },
    )

    assert registry.get_tools_for_agent("empty_agent") == []


async def test_duplicate_tool_names_fail_clearly() -> None:
    spec = _spec(domestic_tools=["book_flight"], domestic_mcps=["flights_mcp"])
    registry = await _started_registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([_tool("flights_mcp", "book_flight")]),
            "admin_mcp": FakeMCPClient([]),
        },
    )

    with pytest.raises(ToolRegistryError, match="Duplicate runtime tool name 'book_flight'"):
        registry.get_tools_for_agent("domestic_flights_agent")


async def test_call_tool_succeeds_for_allowed_mcp_tool() -> None:
    spec = _spec(domestic_mcps=["flights_mcp"], super_mcps=["admin_mcp"])
    flights_client = FakeMCPClient([_tool("flights_mcp", "flights_search")])
    registry = await _started_registry(
        spec,
        {
            "flights_mcp": flights_client,
            "admin_mcp": FakeMCPClient([_tool("admin_mcp", "admin_action")]),
        },
    )
    ctx = ExecutionContext(message="hello", state={})
    arguments: dict[str, object] = {"origin": "TLV"}

    result = await registry.call_tool(
        agent_id="domestic_flights_agent",
        tool_name="flights_search",
        arguments=arguments,
        ctx=ctx,
    )

    assert result == {"tool": "flights_search", "arguments": arguments}
    assert flights_client.calls == [("flights_search", arguments)]


async def test_call_tool_fails_for_tool_not_allowed_for_agent() -> None:
    spec = _spec(domestic_mcps=["flights_mcp"], super_mcps=["admin_mcp"])
    registry = await _started_registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([_tool("flights_mcp", "flights_search")]),
            "admin_mcp": FakeMCPClient([_tool("admin_mcp", "admin_action")]),
        },
    )
    ctx = ExecutionContext(message="hello", state={})

    with pytest.raises(
        ToolRegistryError,
        match="Tool 'admin_action' is not allowed for agent 'domestic_flights_agent'",
    ):
        await registry.call_tool(
            agent_id="domestic_flights_agent",
            tool_name="admin_action",
            arguments={},
            ctx=ctx,
        )


def test_unknown_agent_id_fails_clearly() -> None:
    spec = _spec()
    registry = _registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([]),
            "admin_mcp": FakeMCPClient([]),
        },
    )

    with pytest.raises(ToolRegistryError, match="Unknown agent 'missing_agent'"):
        registry.get_tools_for_agent("missing_agent")


async def test_unknown_tool_name_fails_clearly() -> None:
    spec = _spec(domestic_mcps=["flights_mcp"])
    registry = await _started_registry(
        spec,
        {
            "flights_mcp": FakeMCPClient([_tool("flights_mcp", "flights_search")]),
            "admin_mcp": FakeMCPClient([]),
        },
    )
    ctx = ExecutionContext(message="hello", state={})

    with pytest.raises(
        ToolRegistryError,
        match="Tool 'missing_tool' is not allowed for agent 'domestic_flights_agent'",
    ):
        await registry.call_tool(
            agent_id="domestic_flights_agent",
            tool_name="missing_tool",
            arguments={},
            ctx=ctx,
        )
