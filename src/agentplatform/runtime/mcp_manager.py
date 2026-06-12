from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.tool_models import MCPToolDefinition
from agentplatform.spec.models import McpSpec


class MCPManagerError(RuntimeError):
    """Raised when MCP manager operations fail."""


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
    raise MCPManagerError(
        f"Real MCP client is not implemented yet for MCP server '{server_id}'. "
        "Inject an MCPClientFactory for tests or implement the remote MCP client in a later phase."
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

    async def start(self) -> None:
        for server_id, config in self._mcp_configs.items():
            client = self._client_factory(server_id, config)

            try:
                await client.connect()
                tools = await client.list_tools()
            except Exception as exc:
                raise MCPManagerError(
                    f"MCP server '{server_id}' failed to start/discover tools: {exc}"
                ) from exc

            self._clients[server_id] = client
            self._tools_by_server[server_id] = tools

    async def stop(self) -> None:
        errors: list[str] = []

        for server_id, client in self._clients.items():
            try:
                await client.close()
            except Exception as exc:
                errors.append(f"{server_id}: {exc}")

        self._clients.clear()
        self._tools_by_server.clear()

        if errors:
            raise MCPManagerError("Failed to close MCP clients: " + "; ".join(errors))

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
        if server_id not in self._clients:
            raise MCPManagerError(f"MCP server '{server_id}' is not started.")

        known_tools = {tool.name for tool in self.list_tools(server_id)}
        if tool_name not in known_tools:
            raise MCPManagerError(f"Unknown MCP tool '{tool_name}' for server '{server_id}'.")

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
