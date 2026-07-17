"""Streamable HTTP MCP server for deterministic enterprise knowledge tools."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from . import SERVER_ID
from . import tools as knowledge


def create_server(*, host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    server = FastMCP(
        "Enterprise Local Knowledge",
        instructions="Deterministic local tools for session-approval verification.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.tool()
    def search_internal_documents(query: str) -> list[knowledge.Document]:
        """Search mock internal company documents."""
        _log_execution("search_internal_documents")
        return knowledge.search_internal_documents(query)

    @server.tool()
    def get_employee_information(
        employee_id: str,
    ) -> knowledge.Employee | dict[str, str]:
        """Return mock employee and team information."""
        _log_execution("get_employee_information")
        return knowledge.get_employee_information(employee_id)

    @server.tool()
    def publish_internal_note(title: str, content: str) -> knowledge.PublishedNote:
        """Simulate publishing an internal note without persisting it."""
        _log_execution("publish_internal_note")
        return knowledge.publish_internal_note(title, content)

    return server


def _log_execution(tool_name: str) -> None:
    print(
        f"[LOCAL MCP TOOL] server={SERVER_ID} tool={tool_name} executed=true",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local enterprise knowledge MCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    create_server(host=args.host, port=args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
