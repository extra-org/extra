from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from urllib.parse import urlparse

from agent_engine.logging_setup import sanitize_url_for_logging
from agent_engine.runtime.mcp_manager import MCPClientProtocol
from agent_engine.runtime.tool_models import MCPToolDefinition

logger = logging.getLogger(__name__)


class RemoteMCPClientError(RuntimeError):
    """Raised when the generic remote MCP client cannot complete an operation."""


class AsyncContextManagerFactory(Protocol):
    def __call__(self, url: str) -> Any: ...


class SessionFactory(Protocol):
    def __call__(self, read_stream: Any, write_stream: Any) -> Any: ...


@dataclass
class _ClientCommand:
    kind: str
    future: asyncio.Future[object]
    tool_name: str | None = None
    arguments: dict[str, object] | None = None


class GenericRemoteMCPClient(MCPClientProtocol):
    """Generic URL-based MCP client backed by the official MCP Python SDK.

    The rest of the runtime sees only ``MCPClientProtocol`` and
    ``MCPToolDefinition``. SDK-specific session, transport, and result objects
    stay inside this adapter.
    """

    _SUPPORTED_TRANSPORTS: ClassVar[set[str]] = {"streamable_http", "streamable-http"}

    def __init__(
        self,
        *,
        server_id: str,
        url: str,
        transport: str = "streamable_http",
        transport_factory: AsyncContextManagerFactory | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.server_id = server_id
        self.url = _validate_url(server_id, url)
        self.transport = _normalize_transport(server_id, transport)
        self._transport_factory = transport_factory or _streamable_http_transport
        self._session_factory = session_factory or _client_session
        self._commands: asyncio.Queue[_ClientCommand] | None = None
        self._session_task: asyncio.Task[None] | None = None
        logger.debug(
            "Initialized remote MCP client server=%s url=%s transport=%s",
            server_id,
            sanitize_url_for_logging(self.url),
            self.transport,
        )

    async def connect(self) -> None:
        if self._session_task is not None:
            return

        logger.debug(
            "Connecting remote MCP client server=%s url=%s",
            self.server_id,
            sanitize_url_for_logging(self.url),
        )

        commands: asyncio.Queue[_ClientCommand] = asyncio.Queue()
        ready: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._run_session(commands, ready))
        self._commands = commands
        self._session_task = task

        try:
            await ready
        except Exception:
            await self._cancel_session_task()
            raise

    async def close(self) -> None:
        task = self._session_task
        commands = self._commands

        if task is None or commands is None:
            return

        logger.debug("Closing remote MCP client server=%s", self.server_id)
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        await commands.put(_ClientCommand(kind="close", future=future))
        await future
        await task
        self._session_task = None
        self._commands = None

    async def list_tools(self) -> list[MCPToolDefinition]:
        result = await self._send_command("list_tools")
        if not isinstance(result, list):
            raise RemoteMCPClientError(
                f"MCP server '{self.server_id}' returned invalid tools/list response."
            )
        return result

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> object:
        return await self._send_command("call_tool", tool_name=name, arguments=arguments)

    async def _run_session(
        self,
        commands: asyncio.Queue[_ClientCommand],
        ready: asyncio.Future[object],
    ) -> None:
        close_future: asyncio.Future[object] | None = None
        try:
            async with AsyncExitStack() as stack:
                try:
                    streams = await stack.enter_async_context(self._transport_factory(self.url))
                except Exception as exc:
                    raise RemoteMCPClientError(
                        f"MCP server '{self.server_id}' failed to connect to '{self.url}': {exc}"
                    ) from exc

                read_stream, write_stream = _extract_transport_streams(self.server_id, streams)
                session = await stack.enter_async_context(
                    self._session_factory(read_stream, write_stream)
                )

                try:
                    await session.initialize()
                except Exception as exc:
                    raise RemoteMCPClientError(
                        f"MCP server '{self.server_id}' failed to initialize MCP session: {exc}"
                    ) from exc

                logger.debug("MCP session established server=%s", self.server_id)
                ready.set_result(None)

                while True:
                    command = await commands.get()
                    if command.kind == "close":
                        close_future = command.future
                        break
                    await self._handle_command(session, command)
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, RemoteMCPClientError)
                else RemoteMCPClientError(f"MCP server '{self.server_id}' session failed: {exc}")
            )
            if not ready.done():
                ready.set_exception(error)
            if close_future is not None and not close_future.done():
                close_future.set_exception(error)
            raise error from exc
        else:
            if close_future is not None and not close_future.done():
                close_future.set_result(None)

    async def _handle_command(self, session: Any, command: _ClientCommand) -> None:
        try:
            if command.kind == "list_tools":
                logger.debug("tools/list started server=%s", self.server_id)
                result = await session.list_tools()
                tools = getattr(result, "tools", None)
                if not isinstance(tools, list):
                    raise RemoteMCPClientError(
                        f"MCP server '{self.server_id}' returned invalid tools/list response."
                    )
                logger.debug("tools/list completed server=%s tools=%d", self.server_id, len(tools))
                command.future.set_result(
                    [_tool_definition_from_sdk_tool(self.server_id, tool) for tool in tools]
                )
                return

            if command.kind == "call_tool":
                if command.tool_name is None or command.arguments is None:
                    raise RemoteMCPClientError(
                        f"MCP server '{self.server_id}' received invalid tool command."
                    )
                logger.debug(
                    "tools/call started server=%s tool=%s", self.server_id, command.tool_name
                )
                result = await session.call_tool(command.tool_name, command.arguments)
                logger.debug(
                    "tools/call completed server=%s tool=%s", self.server_id, command.tool_name
                )
                command.future.set_result(
                    _normalize_call_tool_result(self.server_id, command.tool_name, result)
                )
                return

            raise RemoteMCPClientError(
                f"MCP server '{self.server_id}' received unknown client command '{command.kind}'."
            )
        except Exception as exc:
            if command.future.done():
                return
            if isinstance(exc, RemoteMCPClientError):
                command.future.set_exception(exc)
            elif command.kind == "list_tools":
                command.future.set_exception(
                    RemoteMCPClientError(
                        f"MCP server '{self.server_id}' failed to discover tools: {exc}"
                    )
                )
            elif command.kind == "call_tool":
                command.future.set_exception(
                    RemoteMCPClientError(
                        f"MCP tool '{command.tool_name}' on server '{self.server_id}' failed: {exc}"
                    )
                )
            else:
                command.future.set_exception(exc)

    async def _send_command(
        self,
        kind: str,
        *,
        tool_name: str | None = None,
        arguments: dict[str, object] | None = None,
    ) -> object:
        commands = self._commands
        task = self._session_task
        if commands is None or task is None:
            raise RemoteMCPClientError(
                f"MCP server '{self.server_id}' client used before connect()."
            )

        if task.done():
            raise RemoteMCPClientError(f"MCP server '{self.server_id}' session is not running.")

        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        await commands.put(
            _ClientCommand(
                kind=kind,
                future=future,
                tool_name=tool_name,
                arguments=arguments,
            )
        )
        return await future

    async def _cancel_session_task(self) -> None:
        task = self._session_task
        self._session_task = None
        self._commands = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError, RemoteMCPClientError):
            await task


def _validate_url(server_id: str, url: str) -> str:
    if not url:
        raise RemoteMCPClientError(f"MCP server '{server_id}' must define a non-empty URL.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RemoteMCPClientError(
            f"MCP server '{server_id}' has invalid URL '{url}'. "
            "Remote MCP URLs must be absolute http(s) URLs."
        )

    return url


def _normalize_transport(server_id: str, transport: str) -> str:
    if transport not in GenericRemoteMCPClient._SUPPORTED_TRANSPORTS:
        raise RemoteMCPClientError(
            f"MCP server '{server_id}' has unsupported remote MCP transport "
            f"'{transport}'. Supported transport: streamable_http."
        )
    return "streamable_http"


def _streamable_http_transport(url: str) -> Any:
    try:
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:  # pragma: no cover - exercised only without dependency installed.
        raise RemoteMCPClientError(
            "The official MCP Python SDK is required for remote MCP clients. "
            'Install the project with the "mcp>=1.27,<2" dependency.'
        ) from exc

    return streamable_http_client(url)


def _client_session(read_stream: Any, write_stream: Any) -> Any:
    try:
        from mcp import ClientSession
    except ImportError as exc:  # pragma: no cover - exercised only without dependency installed.
        raise RemoteMCPClientError(
            "The official MCP Python SDK is required for remote MCP clients. "
            'Install the project with the "mcp>=1.27,<2" dependency.'
        ) from exc

    return ClientSession(read_stream, write_stream)


def _extract_transport_streams(server_id: str, streams: object) -> tuple[object, object]:
    if not isinstance(streams, tuple) or len(streams) < 2:
        raise RemoteMCPClientError(f"MCP server '{server_id}' returned invalid transport streams.")

    return streams[0], streams[1]


def _tool_definition_from_sdk_tool(server_id: str, tool: object) -> MCPToolDefinition:
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name:
        raise RemoteMCPClientError(
            f"MCP server '{server_id}' returned invalid tool metadata: missing tool name."
        )

    description = getattr(tool, "description", "") or ""
    if not isinstance(description, str):
        raise RemoteMCPClientError(
            f"MCP server '{server_id}' returned invalid metadata for tool '{name}'."
        )

    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = {}

    parameters_schema = _json_object(server_id, name, schema)
    return MCPToolDefinition(
        server_id=server_id,
        name=name,
        description=description,
        parameters_schema=parameters_schema,
    )


def _json_object(server_id: str, tool_name: str, value: object) -> dict[str, object]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, mode="json")

    if isinstance(value, dict):
        return dict(value)

    raise RemoteMCPClientError(
        f"MCP server '{server_id}' returned invalid parameters schema for tool '{tool_name}'."
    )


def _normalize_call_tool_result(server_id: str, tool_name: str, result: object) -> object:
    if bool(getattr(result, "isError", False)):
        raise RemoteMCPClientError(
            f"MCP tool '{tool_name}' on server '{server_id}' returned error."
        )

    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        return _json_value(structured)

    content = getattr(result, "content", None)
    if isinstance(content, list):
        normalized = [_normalize_content_block(block) for block in content]
        if len(normalized) == 1:
            return normalized[0]
        return normalized

    raise RemoteMCPClientError(
        f"MCP tool '{tool_name}' on server '{server_id}' returned an unsupported result shape."
    )


def _normalize_content_block(block: object) -> object:
    text = getattr(block, "text", None)
    if isinstance(text, str):
        return text

    return _json_value(block)


def _json_value(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    return value
