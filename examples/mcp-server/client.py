"""Tiny MCP client that talks to ``agentctl mcp serve``.

Start the server in one terminal::

    agentctl mcp serve --config examples/mcp-server/agents.yaml

Then run this in another::

    python examples/mcp-server/client.py

It discovers the ``extra_chat`` tool, sends a message, follows up in the
same session, and verifies that two different sessions keep independent
history.
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call_chat(
    session: ClientSession, message: str, session_id: str | None = None
) -> dict:
    args: dict[str, str] = {"message": message}
    if session_id:
        args["session_id"] = session_id
    result = await session.call_tool("extra_chat", args)
    if result.isError:
        raise RuntimeError(f"tool call failed: {result.content}")
    for block in result.content:
        if getattr(block, "text", None):
            return json.loads(block.text)
    raise RuntimeError("no text in tool response")


async def main() -> None:
    server_cmd = [
        sys.executable,
        "-m",
        "agentctl",
        "mcp",
        "serve",
        "--config",
        "examples/mcp-server/agents.yaml",
    ]
    params = StdioServerParameters(command=server_cmd[0], args=server_cmd[1:])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"discovered tools: {names}")
            assert "extra_chat" in names

            first = await call_chat(session, "hello there")
            sid = first["session_id"]
            print(f"first  sid={sid}  answer={first['answer']!r}")

            second = await call_chat(session, "what did I just say?", session_id=sid)
            print(
                f"second sid={second['session_id']}  answer={second['answer']!r}"
            )
            assert second["session_id"] == sid

            fresh = await call_chat(session, "fresh session, no history")
            print(f"third  sid={fresh['session_id']}  answer={fresh['answer']!r}")
            assert fresh["session_id"] != sid


if __name__ == "__main__":
    asyncio.run(main())