# MCP & Tools

This document defines how executor agents use Python plugin tools and MCP
servers. Python plugin tools, the generic tool-runtime boundary, a generic
URL-based remote MCP client, and LangGraph/LangChain binding for discovered MCP
tools are implemented.

---

## MCP Servers

MCP servers are declared once and referenced by agents:

```yaml
mcps:
  flights_mcp:
    url: "https://company.com/mcp/flights"

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    mcps: [flights_mcp]
```

MCP servers may be implemented in any language. Users only declare the server
URL in YAML; they do not write MCP client classes. At startup, `Engine.start()`
asks `MCPManager` to create one `GenericRemoteMCPClient` per configured MCP
server URL, connect and initialize the MCP session, discover tools, and cache
the discovered metadata.

The default remote transport is the official MCP Streamable HTTP transport.
The YAML contract remains URL-based; local process / stdio MCP servers are not
supported yet.

---

## Python Plugin Tools

Tools are Python plugin methods exposed to the LLM at runtime. Each tool is
declared with a description in YAML and implemented as a callable in
`plugins/tools/{tool_id}.py`:

```yaml
tools:
  book_flight:
    description: "Search and book a flight given origin, destination and travel date"

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    tools: [book_flight]
```

Plugin file (`plugins/tools/book_flight.py`):

```python
def book_flight() -> str:
    """Search and book a flight given origin, destination and travel date."""
    raise NotImplementedError
```

Run `agentctl generate` to create tool stubs. The engine loads each tool once at
graph-build time and wraps it as a LangChain `StructuredTool`. At runtime, only
the agent's declared tools are bound to its LLM, and a tool-call loop runs until
the model stops requesting tools.

---

## Runtime Tool Boundary

The runtime-facing abstraction is `ToolRegistry`. It exposes model-facing
`RuntimeTool` metadata:

- `name`
- `description`
- `parameters_schema`

The model-facing metadata intentionally hides whether a tool came from a local
Python plugin or an MCP server. Internal routing metadata stays in
`RuntimeToolBinding`, `ToolRegistry`, `MCPToolProvider`, and `MCPManager`.

MCP-backed `RuntimeTool` values are adapted into executable LangChain tools at
agent-node execution time, when the per-request `ExecutionContext` exists. Tool
calls made by the model flow through:

```text
LLM tool call
  → LangChain tool adapter
  → ToolRegistry.call_tool(agent_id, tool_name, arguments, ctx)
  → MCPToolProvider
  → MCPManager
  → GenericRemoteMCPClient
  → remote MCP server
```

LangGraph nodes do not call `MCPManager` or `GenericRemoteMCPClient` directly.
The current agent-node tool loop is synchronous, so the LangChain adapter owns
the sync-to-async bridge into `ToolRegistry.call_tool`. Async-native graph
execution can replace that bridge in a later lifecycle task.

Per-agent access is enforced from declarations:

- local tools come from the selected agent's `tools`;
- MCP tools come only from servers listed in the selected agent's `mcps`;
- duplicate runtime tool names fail clearly.

`ExecutionContext` does not own MCP connections. `Engine` owns one `MCPManager`
and one `ToolRegistry`; `MCPManager` owns the long-lived MCP clients.

`Engine.start()` and `Engine.stop()` are the lifecycle hooks for MCP
connections. `Engine.run()` does not auto-start MCP clients yet, so ordinary CLI
run behavior remains usable without making remote MCP connections.

---

## Resolver vs. Tool Boundary

| | Resolver | Tool |
| --- | --- | --- |
| Runs | Before the node runs | During LLM execution |
| Chosen by | Engine | LLM |
| Exposed to LLM | No | Yes |
| Token cost | None | Yes |
| Purpose | Fill prompt variables | Perform actions |

Use a resolver for deterministic context such as `current_date`, `user_name`, or
`subscription`. Use a tool for model-selected actions such as `book_flight` or
`add_to_cart`.

---

## Safety

The current schema does not yet define per-tool permissions or input policies.
For the MVP:

- validate that every agent tool id exists in top-level `tools` (✅ implemented);
- validate that every agent MCP id exists in top-level `mcps` (✅ implemented);
- load tool plugins from `plugins/tools/{tool_id}.py` (✅ implemented);
- bind only the agent's declared tools per node (✅ implemented);
- pass request context through `ctx` (✅ implemented);
- create generic URL-based remote MCP clients from `mcps.<id>.url` (✅ implemented);
- discover and cache remote MCP tool metadata on `Engine.start()` (✅ implemented);
- hide local-vs-MCP origin behind `ToolRegistry` and `RuntimeTool` (✅ implemented);
- bind discovered MCP tools into LangGraph/LangChain tool-calling (✅ implemented);
- redact secrets from traces (⏳ planned, task 0011);
- keep prompt wording out of the enforcement path.

Future per-tool access control should be added deliberately to the schema and
docs before implementation.
