from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeTool:
    """Model-facing tool metadata.

    This is what the runtime can expose to the LLM/LangGraph.
    It intentionally does not expose whether the tool came from MCP,
    local Python plugins, or another future provider.
    """

    name: str
    description: str
    parameters_schema: dict[str, object]


@dataclass(frozen=True)
class RuntimeToolBinding:
    """Internal runtime binding used to route tool calls."""

    tool: RuntimeTool
    provider_id: str
    internal_tool_name: str
    server_id: str | None = None


@dataclass(frozen=True)
class MCPToolDefinition:
    """Tool metadata discovered from one MCP server."""

    server_id: str
    name: str
    description: str
    parameters_schema: dict[str, object]
