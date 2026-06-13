"""MCP manager boundary behavior with fake in-memory clients."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agent_engine.runtime.context import ExecutionContext
from agent_engine.runtime.mcp_manager import MCPManager, MCPManagerError
from agent_engine.runtime.tool_models import MCPToolDefinition
from agent_engine.spec.models import McpSpec


class FakeMCPClient:
    def __init__(
        self,
        tools: list[MCPToolDefinition],
        *,
        fail_connect: bool = False,
        fail_list_tools: bool = False,
        fail_call_tool: bool = False,
    ) -> None:
        self.tools = tools
        self.connected = False
        self.closed = False
        self.fail_connect = fail_connect
        self.fail_list_tools = fail_list_tools
        self.fail_call_tool = fail_call_tool
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.connect_thread_id: int | None = None
        self.call_thread_id: int | None = None

    async def connect(self) -> None:
        if self.fail_connect:
            raise RuntimeError("connect failed")
        self.connected = True
        self.connect_thread_id = threading.get_ident()

    async def close(self) -> None:
        self.closed = True

    async def list_tools(self) -> list[MCPToolDefinition]:
        if self.fail_list_tools:
            raise RuntimeError("list tools failed")
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        if self.fail_call_tool:
            raise RuntimeError("tool failed")
        self.calls.append((name, arguments))
        self.call_thread_id = threading.get_ident()
        return {"tool": name, "arguments": arguments}


def _tool(server_id: str, name: str) -> MCPToolDefinition:
    return MCPToolDefinition(
        server_id=server_id,
        name=name,
        description=f"{name} description",
        parameters_schema={"type": "object"},
    )


def _manager(
    clients: dict[str, FakeMCPClient],
    configs: dict[str, McpSpec] | None = None,
) -> MCPManager:
    mcp_configs = configs or {
        server_id: McpSpec(url=f"https://example.com/{server_id}") for server_id in clients
    }

    def factory(server_id: str, config: McpSpec) -> FakeMCPClient:
        assert config.url.startswith("https://example.com/")
        return clients[server_id]

    return MCPManager(mcp_configs, client_factory=factory)


def test_initializes_with_url_based_mcp_configs() -> None:
    clients = {"flights_mcp": FakeMCPClient([])}
    manager = _manager(
        clients,
        configs={"flights_mcp": McpSpec(url="https://example.com/flights-mcp")},
    )

    assert manager.list_tools("flights_mcp") == []


async def test_start_connects_to_each_configured_fake_client() -> None:
    clients = {
        "flights_mcp": FakeMCPClient([]),
        "admin_mcp": FakeMCPClient([]),
    }
    manager = _manager(clients)

    await manager.start()

    assert clients["flights_mcp"].connected is True
    assert clients["admin_mcp"].connected is True


async def test_start_discovers_and_caches_tools_from_each_client() -> None:
    clients = {
        "flights_mcp": FakeMCPClient([_tool("flights_mcp", "flights_search")]),
        "admin_mcp": FakeMCPClient([_tool("admin_mcp", "admin_action")]),
    }
    manager = _manager(clients)

    await manager.start()

    assert [tool.name for tool in manager.list_tools("flights_mcp")] == ["flights_search"]
    assert [tool.name for tool in manager.list_tools("admin_mcp")] == ["admin_action"]


def test_list_tools_for_unknown_server_id_raises() -> None:
    manager = _manager({"flights_mcp": FakeMCPClient([])})

    with pytest.raises(MCPManagerError, match="Unknown MCP server 'missing_mcp'"):
        manager.list_tools("missing_mcp")


async def test_call_tool_succeeds_for_known_server_and_tool() -> None:
    client = FakeMCPClient([_tool("flights_mcp", "flights_search")])
    manager = _manager({"flights_mcp": client})
    ctx = ExecutionContext(message="hello", state={}, request_id="req-1")
    arguments: dict[str, object] = {"origin": "TLV"}
    await manager.start()

    result = await manager.call_tool(
        server_id="flights_mcp",
        tool_name="flights_search",
        arguments=arguments,
        ctx=ctx,
    )

    assert result == {"tool": "flights_search", "arguments": arguments}
    assert client.calls == [("flights_search", arguments)]


def test_call_tool_from_another_event_loop_runs_client_on_owner_loop() -> None:
    client = FakeMCPClient([_tool("flights_mcp", "flights_search")])
    manager = _manager({"flights_mcp": client})
    ctx = ExecutionContext(message="hello", state={})
    ready = threading.Event()
    stopped = threading.Event()

    async def start_and_wait() -> None:
        await manager.start()
        ready.set()
        while not stopped.is_set():
            await asyncio.sleep(0.01)
        await manager.stop()

    def run_owner_loop() -> None:
        asyncio.run(start_and_wait())

    thread = threading.Thread(target=run_owner_loop)
    thread.start()
    ready.wait(timeout=5)

    try:
        result = asyncio.run(
            manager.call_tool(
                server_id="flights_mcp",
                tool_name="flights_search",
                arguments={},
                ctx=ctx,
            )
        )
    finally:
        stopped.set()
        thread.join(timeout=5)

    assert result == {"tool": "flights_search", "arguments": {}}
    assert client.connect_thread_id is not None
    assert client.call_thread_id == client.connect_thread_id


async def test_call_tool_for_unknown_server_fails_clearly() -> None:
    manager = _manager({"flights_mcp": FakeMCPClient([])})
    ctx = ExecutionContext(message="hello", state={})

    with pytest.raises(MCPManagerError, match="MCP server 'missing_mcp' is not started"):
        await manager.call_tool(
            server_id="missing_mcp",
            tool_name="anything",
            arguments={},
            ctx=ctx,
        )


async def test_call_tool_for_unknown_tool_fails_clearly() -> None:
    manager = _manager({"flights_mcp": FakeMCPClient([_tool("flights_mcp", "flights_search")])})
    ctx = ExecutionContext(message="hello", state={})
    await manager.start()

    with pytest.raises(
        MCPManagerError,
        match="Unknown MCP tool 'admin_action' for server 'flights_mcp'",
    ):
        await manager.call_tool(
            server_id="flights_mcp",
            tool_name="admin_action",
            arguments={},
            ctx=ctx,
        )


async def test_successful_call_records_started_and_succeeded_trace_events() -> None:
    manager = _manager({"flights_mcp": FakeMCPClient([_tool("flights_mcp", "flights_search")])})
    ctx = ExecutionContext(message="hello", state={}, request_id="req-1")
    await manager.start()

    await manager.call_tool(
        server_id="flights_mcp",
        tool_name="flights_search",
        arguments={"origin": "TLV"},
        ctx=ctx,
    )

    assert [event["type"] for event in ctx.trace_events] == [
        "tool_call_started",
        "tool_call_succeeded",
    ]
    assert ctx.trace_events[0]["request_id"] == "req-1"
    assert ctx.trace_events[0]["arguments"] == {"origin": "TLV"}


async def test_failing_call_records_started_and_failed_trace_events() -> None:
    manager = _manager(
        {
            "flights_mcp": FakeMCPClient(
                [_tool("flights_mcp", "flights_search")], fail_call_tool=True
            )
        }
    )
    ctx = ExecutionContext(message="hello", state={}, request_id="req-1")
    await manager.start()

    with pytest.raises(MCPManagerError, match="MCP tool 'flights_search' on server"):
        await manager.call_tool(
            server_id="flights_mcp",
            tool_name="flights_search",
            arguments={},
            ctx=ctx,
        )

    assert [event["type"] for event in ctx.trace_events] == [
        "tool_call_started",
        "tool_call_failed",
    ]
    assert ctx.trace_events[1]["error"] == "tool failed"


async def test_stop_closes_all_connected_fake_clients() -> None:
    clients = {
        "flights_mcp": FakeMCPClient([]),
        "admin_mcp": FakeMCPClient([]),
    }
    manager = _manager(clients)
    await manager.start()

    await manager.stop()

    assert clients["flights_mcp"].closed is True
    assert clients["admin_mcp"].closed is True


async def test_start_failure_includes_mcp_server_id() -> None:
    manager = _manager({"flights_mcp": FakeMCPClient([], fail_connect=True)})

    with pytest.raises(
        MCPManagerError,
        match="MCP server 'flights_mcp' failed to start/discover tools",
    ):
        await manager.start()


async def test_discovery_failure_includes_mcp_server_id() -> None:
    manager = _manager({"flights_mcp": FakeMCPClient([], fail_list_tools=True)})

    with pytest.raises(
        MCPManagerError,
        match="MCP server 'flights_mcp' failed to start/discover tools",
    ):
        await manager.start()
