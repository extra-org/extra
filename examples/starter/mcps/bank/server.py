"""A tiny MCP server, so the example needs no external service.

It speaks the same Streamable HTTP protocol a hosted MCP would, so nothing about
the integration is simulated — only the data behind it.

    python -m mcps.bank.server            # from examples/starter
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from . import data


def create_server(*, host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    server = FastMCP(
        "Bank Core",
        instructions="Read-only account data for the starter example.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.tool()
    def get_accounts() -> list[data.Account]:
        """List the customer's accounts with their current balances."""
        print("[BANK MCP] get_accounts", flush=True)
        return data.list_accounts()

    @server.tool()
    def get_transactions(limit: int = 5) -> list[data.Transaction]:
        """List the customer's most recent transactions, newest first."""
        print(f"[BANK MCP] get_transactions limit={limit}", flush=True)
        return data.recent_transactions(limit)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the starter example's bank MCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    create_server(host=args.host, port=args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
