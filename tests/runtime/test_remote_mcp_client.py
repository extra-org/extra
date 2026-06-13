"""Generic URL-based remote MCP client adapter tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from agent_engine.runtime.mcp_manager import MCPClientProtocol, _default_client_factory
from agent_engine.runtime.remote_mcp_client import GenericRemoteMCPClient, RemoteMCPClientError
from agent_engine.spec.models import McpSpec


class FakeTransportContext:
    def __init__(
        self,
        *,
        fail_enter: bool = False,
        streams: object = ("read-stream", "write-stream"),
    ) -> None:
        self.fail_enter = fail_enter
        self.streams = streams
        self.entered = False
        self.closed = False
        self.enter_task: object | None = None
        self.exit_task: object | None = None

    async def __aenter__(self) -> object:
        if self.fail_enter:
            raise RuntimeError("transport failed")
        self.entered = True
        self.enter_task = asyncio.current_task()
        return self.streams

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True
        self.exit_task = asyncio.current_task()


class FakeSessionContext:
    def __init__(
        self,
        session: FakeSession,
        *,
        fail_enter: bool = False,
    ) -> None:
        self.session = session
        self.fail_enter = fail_enter
        self.entered = False
        self.closed = False

    async def __aenter__(self) -> FakeSession:
        if self.fail_enter:
            raise RuntimeError("session failed")
        self.entered = True
        return self.session

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True


@dataclass
class FakeSDKTool:
    name: str
    description: str = "tool description"
    inputSchema: dict[str, object] | None = None


@dataclass
class InvalidSDKTool:
    description: str = "missing name"
    inputSchema: dict[str, object] | None = None


@dataclass
class FakeListToolsResult:
    tools: list[object]


@dataclass
class FakeTextContent:
    text: str


@dataclass
class FakeCallToolResult:
    content: list[object] | None = None
    structuredContent: object | None = None
    isError: bool = False


class FakeSession:
    def __init__(
        self,
        *,
        tools: list[object] | None = None,
        call_result: object | None = None,
        fail_initialize: bool = False,
        fail_list_tools: bool = False,
        fail_call_tool: bool = False,
    ) -> None:
        self.tools = tools or []
        self.call_result = call_result or FakeCallToolResult(content=[FakeTextContent("ok")])
        self.fail_initialize = fail_initialize
        self.fail_list_tools = fail_list_tools
        self.fail_call_tool = fail_call_tool
        self.initialized = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def initialize(self) -> None:
        if self.fail_initialize:
            raise RuntimeError("initialize failed")
        self.initialized = True

    async def list_tools(self) -> FakeListToolsResult:
        if self.fail_list_tools:
            raise RuntimeError("list failed")
        return FakeListToolsResult(tools=self.tools)

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        if self.fail_call_tool:
            raise RuntimeError("call failed")
        self.calls.append((name, arguments))
        return self.call_result


def _client(
    *,
    session: FakeSession | None = None,
    transport: FakeTransportContext | None = None,
) -> tuple[GenericRemoteMCPClient, FakeSession, FakeTransportContext, FakeSessionContext]:
    fake_session = session or FakeSession()
    fake_transport = transport or FakeTransportContext()
    fake_session_context = FakeSessionContext(fake_session)

    client = GenericRemoteMCPClient(
        server_id="flights_mcp",
        url="https://example.com/mcp/flights",
        transport_factory=lambda url: fake_transport,
        session_factory=lambda read, write: fake_session_context,
    )
    return client, fake_session, fake_transport, fake_session_context


def test_generic_remote_mcp_client_implements_protocol() -> None:
    client, _, _, _ = _client()

    assert isinstance(client, MCPClientProtocol)


async def test_connect_initializes_remote_mcp_session() -> None:
    client, session, transport, session_context = _client()

    await client.connect()

    assert transport.entered is True
    assert session_context.entered is True
    assert session.initialized is True


async def test_list_tools_converts_sdk_tools_to_mcp_tool_definitions() -> None:
    sdk_tool = FakeSDKTool(
        name="flights_search",
        description="Search flights",
        inputSchema={"type": "object", "properties": {"origin": {"type": "string"}}},
    )
    client, _, _, _ = _client(session=FakeSession(tools=[sdk_tool]))
    await client.connect()

    tools = await client.list_tools()

    assert len(tools) == 1
    assert tools[0].server_id == "flights_mcp"
    assert tools[0].name == "flights_search"
    assert tools[0].description == "Search flights"
    assert tools[0].parameters_schema == {
        "type": "object",
        "properties": {"origin": {"type": "string"}},
    }


async def test_call_tool_delegates_to_sdk_and_returns_structured_result() -> None:
    call_result = FakeCallToolResult(structuredContent={"fare": 123})
    client, session, _, _ = _client(session=FakeSession(call_result=call_result))
    arguments: dict[str, object] = {"origin": "TLV"}
    await client.connect()

    result = await client.call_tool("flights_search", arguments)

    assert result == {"fare": 123}
    assert session.calls == [("flights_search", arguments)]


async def test_call_tool_returns_text_content_result() -> None:
    call_result = FakeCallToolResult(content=[FakeTextContent("flight found")])
    client, _, _, _ = _client(session=FakeSession(call_result=call_result))
    await client.connect()

    result = await client.call_tool("flights_search", {})

    assert result == "flight found"


async def test_close_closes_underlying_session_and_transport() -> None:
    client, _, transport, session_context = _client()
    await client.connect()

    await client.close()

    assert session_context.closed is True
    assert transport.closed is True
    assert transport.enter_task is not None
    assert transport.enter_task is transport.exit_task


async def test_connection_failure_produces_clear_error() -> None:
    client, _, _, _ = _client(transport=FakeTransportContext(fail_enter=True))

    with pytest.raises(
        RemoteMCPClientError,
        match="MCP server 'flights_mcp' failed to connect",
    ):
        await client.connect()


async def test_initialization_failure_produces_clear_error() -> None:
    client, _, transport, _ = _client(session=FakeSession(fail_initialize=True))

    with pytest.raises(
        RemoteMCPClientError,
        match="MCP server 'flights_mcp' failed to initialize MCP session",
    ):
        await client.connect()

    assert transport.closed is True


async def test_list_tools_failure_produces_clear_error() -> None:
    client, _, _, _ = _client(session=FakeSession(fail_list_tools=True))
    await client.connect()

    with pytest.raises(
        RemoteMCPClientError,
        match="MCP server 'flights_mcp' failed to discover tools",
    ):
        await client.list_tools()


async def test_call_tool_failure_produces_clear_error() -> None:
    client, _, _, _ = _client(session=FakeSession(fail_call_tool=True))
    await client.connect()

    with pytest.raises(
        RemoteMCPClientError,
        match="MCP tool 'flights_search' on server 'flights_mcp' failed",
    ):
        await client.call_tool("flights_search", {})


async def test_invalid_tool_metadata_produces_clear_error() -> None:
    client, _, _, _ = _client(session=FakeSession(tools=[InvalidSDKTool()]))
    await client.connect()

    with pytest.raises(
        RemoteMCPClientError,
        match="invalid tool metadata: missing tool name",
    ):
        await client.list_tools()


async def test_use_before_connect_fails_clearly() -> None:
    client, _, _, _ = _client()

    with pytest.raises(RemoteMCPClientError, match="used before connect"):
        await client.list_tools()


def test_invalid_url_fails_clearly() -> None:
    with pytest.raises(RemoteMCPClientError, match="invalid URL"):
        GenericRemoteMCPClient(server_id="flights_mcp", url="not-a-url")


def test_unsupported_transport_fails_clearly() -> None:
    with pytest.raises(RemoteMCPClientError, match="unsupported remote MCP transport"):
        GenericRemoteMCPClient(
            server_id="flights_mcp",
            url="https://example.com/mcp/flights",
            transport="stdio",
        )


def test_default_mcp_manager_factory_creates_generic_remote_client_from_url() -> None:
    client = _default_client_factory(
        "flights_mcp",
        McpSpec(url="https://example.com/mcp/flights"),
    )

    assert isinstance(client, GenericRemoteMCPClient)
    assert client.server_id == "flights_mcp"
    assert client.url == "https://example.com/mcp/flights"


async def test_transport_factory_receives_configured_url_on_connect() -> None:
    seen_urls: list[str] = []
    session = FakeSession()
    session_context = FakeSessionContext(session)

    def transport_factory(url: str) -> FakeTransportContext:
        seen_urls.append(url)
        return FakeTransportContext()

    client = GenericRemoteMCPClient(
        server_id="flights_mcp",
        url="https://example.com/mcp/flights",
        transport_factory=transport_factory,
        session_factory=lambda read, write: session_context,
    )

    await client.connect()

    assert seen_urls == ["https://example.com/mcp/flights"]


async def test_call_tool_error_result_fails_clearly() -> None:
    client, _, _, _ = _client(session=FakeSession(call_result=FakeCallToolResult(isError=True)))
    await client.connect()

    with pytest.raises(RemoteMCPClientError, match="returned error"):
        await client.call_tool("flights_search", {})
