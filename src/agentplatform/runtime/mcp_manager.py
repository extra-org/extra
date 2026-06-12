from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Protocol, runtime_checkable

from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.tool_models import MCPToolDefinition
from agentplatform.spec.models import McpSpec

logger = logging.getLogger(__name__)


class MCPManagerError(RuntimeError):
    """Raised when MCP manager operations fail."""


@runtime_checkable
class MCPClientProtocol(Protocol):
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def list_tools(self) -> list[MCPToolDefinition]: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> object: ...


MCPClientFactory = Callable[[str, McpSpec], MCPClientProtocol]


def _default_client_factory(server_id: str, config: McpSpec) -> MCPClientProtocol:
    from agentplatform.runtime.remote_mcp_client import GenericRemoteMCPClient

    return GenericRemoteMCPClient(
        server_id=server_id,
        url=config.url,
    )


class MCPManager:
    """Owns MCP clients, discovered tools, and MCP tool execution."""

    def __init__(
        self,
        mcp_configs: dict[str, McpSpec],
        client_factory: MCPClientFactory | None = None,
    ) -> None:
        self._mcp_configs = mcp_configs
        self._client_factory = client_factory or _default_client_factory
        self._clients: dict[str, MCPClientProtocol] = {}
        self._tools_by_server: dict[str, list[MCPToolDefinition]] = {}
        self._owner_loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._owner_loop = asyncio.get_running_loop()

        logger.info("MCPManager.start() — configured servers=%s", sorted(self._mcp_configs))
        for server_id, config in self._mcp_configs.items():
            logger.info("Creating MCP client for server=%s", server_id)
            client = self._client_factory(server_id, config)

            try:
                logger.info("Connecting to MCP server=%s", server_id)
                await client.connect()
                logger.info("Connected to MCP server=%s; discovering tools", server_id)
                tools = await client.list_tools()
            except Exception as exc:
                logger.error("MCP server=%s failed to start/discover tools: %s", server_id, exc)
                raise MCPManagerError(
                    f"MCP server '{server_id}' failed to start/discover tools: {exc}"
                ) from exc

            if not tools:
                logger.warning("MCP server=%s discovered no tools", server_id)
            else:
                logger.info(
                    "MCP server=%s discovered tools=%d names=%s",
                    server_id,
                    len(tools),
                    sorted(tool.name for tool in tools),
                )
            self._clients[server_id] = client
            self._tools_by_server[server_id] = tools

    async def stop(self) -> None:
        logger.info("MCPManager.stop() — closing clients=%s", sorted(self._clients))
        errors: list[str] = []

        for server_id, client in self._clients.items():
            try:
                await client.close()
            except Exception as exc:
                logger.error("Failed to close MCP server=%s: %s", server_id, exc)
                errors.append(f"{server_id}: {exc}")

        self._clients.clear()
        self._tools_by_server.clear()
        self._owner_loop = None

        if errors:
            raise MCPManagerError("Failed to close MCP clients: " + "; ".join(errors))
        logger.debug("MCPManager.stop() completed; all MCP clients closed")

    def list_tools(self, server_id: str) -> list[MCPToolDefinition]:
        if server_id not in self._mcp_configs:
            raise MCPManagerError(f"Unknown MCP server '{server_id}'.")

        return list(self._tools_by_server.get(server_id, []))

    async def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        owner_loop = self._owner_loop
        running_loop = asyncio.get_running_loop()

        if owner_loop is not None and owner_loop is not running_loop:
            future: Future[object] = asyncio.run_coroutine_threadsafe(
                self._call_tool_on_owner_loop(
                    server_id=server_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    ctx=ctx,
                ),
                owner_loop,
            )
            return await asyncio.wrap_future(future)

        return await self._call_tool_on_owner_loop(
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            ctx=ctx,
        )

    async def _call_tool_on_owner_loop(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        if server_id not in self._clients:
            raise MCPManagerError(f"MCP server '{server_id}' is not started.")

        known_tools = {tool.name for tool in self.list_tools(server_id)}
        if tool_name not in known_tools:
            raise MCPManagerError(f"Unknown MCP tool '{tool_name}' for server '{server_id}'.")

        logger.info("Calling MCP tool=%s on server=%s", tool_name, server_id)
        logger.debug(
            "MCP tool=%s on server=%s argument keys=%s",
            tool_name,
            server_id,
            sorted(arguments.keys()),
        )
        started = time.perf_counter()

        ctx.add_trace_event(
            {
                "type": "tool_call_started",
                "request_id": ctx.request_id,
                "provider": "mcp",
                "server_id": server_id,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )

        try:
            result = await self._clients[server_id].call_tool(tool_name, arguments)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "MCP tool=%s on server=%s failed after %dms: %s",
                tool_name,
                server_id,
                duration_ms,
                exc,
            )
            ctx.add_trace_event(
                {
                    "type": "tool_call_failed",
                    "request_id": ctx.request_id,
                    "provider": "mcp",
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                }
            )
            raise MCPManagerError(
                f"MCP tool '{tool_name}' on server '{server_id}' failed: {exc}"
            ) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("MCP tool=%s on server=%s succeeded in %dms", tool_name, server_id, duration_ms)
        ctx.add_trace_event(
            {
                "type": "tool_call_succeeded",
                "request_id": ctx.request_id,
                "provider": "mcp",
                "server_id": server_id,
                "tool_name": tool_name,
                "duration_ms": duration_ms,
            }
        )

        return result
