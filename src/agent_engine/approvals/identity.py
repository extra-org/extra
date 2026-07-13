"""Stable, collision-safe tool identity.

The short tool name is *not* a safe key for a session permission: two MCP
servers (or a local plugin and an MCP server) may expose tools with the same
name. The identity therefore namespaces the tool name by its provider and, for
MCP tools, the originating server id — mirroring how the tool registry already
distinguishes tools by ``(server_id, name)``.
"""

from __future__ import annotations

from agent_engine.runtime.tool_models import ToolProviderName

_LOCAL_NAMESPACE = "local"


def tool_identity(*, provider: ToolProviderName, server_id: str | None, tool_name: str) -> str:
    """Return a stable ``provider:namespace:tool`` identifier for a tool.

    Local tools share the synthetic ``local`` namespace; MCP tools are scoped by
    their server id so identical short names on different servers never collide.
    """
    namespace = server_id if provider == "mcp" and server_id else _LOCAL_NAMESPACE
    return f"{provider}:{namespace}:{tool_name}"
