# Local Demo MCP Server

A small local MCP server for deterministic smoke testing of remote MCP tool
discovery, `tool_tags`, and safe auth/header forwarding.

## Run

```bash
python -m examples.local_mcp_server.server
```

It serves Streamable HTTP at `http://127.0.0.1:8765/mcp`.

## Example Configs

- `examples/local_mcp_agent.yml` discovers all tools.
- `examples/local_mcp_agent_invoices.yml` sends `X-MCP-Tool-Tag: invoices`.
- `examples/local_mcp_agent_customers.yml` sends `X-MCP-Tool-Tag: customers`.
- `examples/local_mcp_agent_docs_query.yml` sends `?tag=docs`.

All tool data is deterministic and in-memory. The server never logs or returns
raw authorization tokens; debug tools return only safe auth metadata such as
presence and scheme.
