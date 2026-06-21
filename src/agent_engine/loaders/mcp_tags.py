"""Apply per-server MCP ``tool_tags`` to a connection config at discovery time.

MCP ``tools/list`` and ``langchain-mcp-adapters`` expose no native tag/filter
argument, so tag-aware servers receive the selector at the transport layer —
either as an HTTP header or a URL query parameter. This module turns a server's
``tool_tags`` + ``tool_tag_transport`` into the matching ``headers`` / ``url``
entries on the ``StreamableHttpConnection`` config.

Transport is optional for the common case: when ``tool_tags`` are set but no
``tool_tag_transport`` is given, a default header transport
(``X-MCP-Tool-Tag``) is used. Advanced users may override it (custom header or a
query parameter).

Tags are joined with commas into a single value. The set of tools the server
returns for those tags (typically the union) is **server-defined** — the client
does no local filtering. Tags are not secrets; only their count is logged by the
caller, never their values here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agent_engine.core.spec import MCPSpec, McpToolTagTransport

_VALID_TYPES = ("header", "query_param")

#: Default header used to send the tag selector when no transport is configured.
DEFAULT_TOOL_TAG_HEADER = "X-MCP-Tool-Tag"


class McpToolTagError(RuntimeError):
    """Configured ``tool_tags`` cannot be applied (invalid transport)."""


def effective_tool_tag_transport(spec: MCPSpec) -> McpToolTagTransport | None:
    """Resolve the transport a server's tags will use.

    ``None`` when the server has no tags; the explicit ``tool_tag_transport`` if
    set; otherwise the default header transport (``X-MCP-Tool-Tag``).
    """
    if not spec.tool_tags:
        return None
    if spec.tool_tag_transport is not None:
        return spec.tool_tag_transport
    return McpToolTagTransport(type="header", header_name=DEFAULT_TOOL_TAG_HEADER)


def apply_tool_tags(
    config: dict[str, Any],
    tool_tags: Sequence[str],
    transport: McpToolTagTransport | None,
    *,
    server_id: str = "",
) -> dict[str, Any]:
    """Return ``config`` with the tag selector applied.

    No tags -> returned unchanged (the no-op path). Tags with no explicit
    ``transport`` -> the default header transport is used (never an error, never
    silently ignored). An explicit but invalid transport still fails clearly.
    """
    if not tool_tags:
        return config

    where = f" for MCP server '{server_id}'" if server_id else ""
    if transport is None:
        transport = McpToolTagTransport(type="header", header_name=DEFAULT_TOOL_TAG_HEADER)

    value = ",".join(tool_tags)
    if transport.type == "header":
        if not transport.header_name:
            raise McpToolTagError(f"tool_tag_transport.header_name is required{where}")
        headers = dict(config.get("headers") or {})
        headers[transport.header_name] = value
        config["headers"] = headers
    elif transport.type == "query_param":
        if not transport.param_name:
            raise McpToolTagError(f"tool_tag_transport.param_name is required{where}")
        config["url"] = _with_query_param(str(config["url"]), transport.param_name, value)
    else:
        raise McpToolTagError(
            f"tool_tag_transport.type must be one of {_VALID_TYPES}{where}, got {transport.type!r}"
        )
    return config


def _with_query_param(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    query.append((name, value))
    return urlunsplit(parts._replace(query=urlencode(query)))
