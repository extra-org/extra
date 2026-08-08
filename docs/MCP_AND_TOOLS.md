# MCP & Tools

This document defines how executor agents use Python plugin tools and MCP
servers. Python plugin tools, remote MCP connection via `langchain-mcp-adapters`,
and LangChain binding of both as the model's tools are implemented.

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
URL in YAML; they do not write MCP client classes. During `build()`, the engine
creates one `MultiServerMCPClient` (from `langchain-mcp-adapters`) per configured
server, connects, and discovers its tools via `get_tools()`. A server that is
unreachable is logged as a warning and skipped, so local tools keep working.

The default remote transport is the official MCP Streamable HTTP transport.
The YAML contract remains URL-based; local process / stdio MCP servers are not
supported yet.

### Optional tool-discovery tags (`tool_tags`)

Some MCP servers expose grouped tool sets (e.g. `policies`, `architecture`,
`documentation`, `admin`) and return only the tools for a selector supplied
during discovery. `tool_tags` is an **optional, per-server** list that carries
that selector — and for the common case it's all you need:

```yaml
mcps:
  # No tags — unchanged behavior (every discovered tool is bound).
  deepwiki:
    url: "https://mcp.deepwiki.com/mcp"

  # Recommended: just list the tags. The selector is sent by default as the
  # header `X-MCP-Tool-Tag: policies` (comma-joined for multiple tags).
  docs_platform:
    url: "https://mcp.company.com/mcp"
    tool_tags:
      - "policies"
      - "architecture"          # -> X-MCP-Tool-Tag: policies,architecture
```

**Default transport.** Neither MCP `tools/list` nor `langchain-mcp-adapters` has
a native tag/filter argument, so the selector travels at the transport layer.
When you don't say how, the platform uses a **default header transport**:

- header name: `X-MCP-Tool-Tag`
- value: the tags, comma-joined (e.g. `policies,architecture`)

### Advanced: overriding the transport

`tool_tag_transport` is an **optional advanced override** for servers that
expect a different header or a query parameter:

```yaml
mcps:
  # Custom header name.
  internal_docs_platform:
    url: "https://mcp.company.com/mcp"
    tool_tags: ["policies"]
    tool_tag_transport: { type: header, header_name: "X-Company-MCP-Tag" }

  # Query parameter instead of a header.
  partner_docs_platform:
    url: "https://mcp.company.com/mcp"
    tool_tags: ["policies", "architecture"]
    tool_tag_transport: { type: query_param, param_name: "tag" }
```

- `type: header` → sends header `header_name: <comma-joined tags>`.
- `type: query_param` → appends `param_name=<comma-joined tags>` to the `url`.

Behavior and guarantees:

- **Optional & per-server.** Missing or empty `tool_tags` changes nothing;
  different servers may use different tags; tags never affect local tools or
  other MCP servers.
- **`tool_tag_transport` is optional.** Omit it for the default header; provide
  it only to override. An explicit transport that is invalid (unknown `type`, or
  a missing `header_name`/`param_name`) still fails clearly at parse and build.
  Configured tags are never silently ignored.
- **Server-side filtering, not client-side.** The platform does **not** filter by
  tool-name guessing — it sends the selector and binds exactly the tools the
  server returns. With multiple tags the returned set (union vs. intersection) is
  whatever the server defines. The MCP server must know how to read the selector.
- **Not exposed to the LLM.** Tags affect only discovery/binding; the model only
  ever sees the final discovered tools. They are not instructions, application
  tags, or tracing tags.
- **Hooks are independent.** `before_mcp_request` hooks may still add headers
  (auth, etc.) per request; the tag header/param is separate and composes with
  them. `HookedMCPAuth` is unaffected.
- **Logging.** Only the tag *count*, transport *type*, and whether the default
  was used are logged — never tag values, headers, tokens, or payloads.

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

Both local plugin tools and MCP tools are presented to the model as LangChain
tools, so the model cannot tell where a tool came from. The engine assembles an
agent's tools at **build time** (`_build_agent_tools`):

- each declared local tool is loaded from `plugins/tools/{id}.py` and wrapped as
  a `StructuredTool`;
- MCP tools come only from the servers listed in the agent's `mcps`, taken from
  the `MultiServerMCPClient.get_tools()` results discovered during `build()`.

Both are bound to the agent's model; the tool-call loop runs until the model
stops requesting tools. Each call is recorded in the run's `used_tools` with its
`provider` (`"local"` or `"mcp"`), so the origin is tracked for tracing even
though it is hidden from the model.

The engine is driven as an async context manager: `build()` connects MCP servers
and discovers tools; `close()` (on context exit) releases them. `run()` does not
connect MCP servers on its own — `build()` must run first.

```python
async with LangGraphEngine(base_dir) as engine:
    await engine.build(spec)  # connects MCP servers, discovers tools
    result = await engine.run(message)
```

## Runtime Tool Usage Summary

Every run collects deterministic tool-usage records (`RunResult.used_tools`) on
the runtime tool-execution path — in call order, not inferred from the final
answer or requested from the model. Records from tools called by nested agents
are merged up into the top-level result.

Rendering these records in `agentctl run` is ⏳ **planned** (not yet wired into
the CLI output). The intended format:

```text
tools used:

* ask_question [mcp: deepwiki] succeeded
* read_wiki_structure [mcp: deepwiki] succeeded
```

Local plugin tools are shown as `[local]`:

```text
tools used:

* book_flight [local] succeeded
```

If no tool was called, the CLI prints:

```text
tools used: none
```

Failed calls are shown with a concise error, without full tracebacks or tool
arguments:

```text
* ask_question [mcp: deepwiki] failed: request timed out
```

Every actual tool call is printed in call order. Repeated calls to the same
tool are printed repeatedly rather than collapsed into a count.

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
- create remote MCP clients from `mcps.<id>.url` via `langchain-mcp-adapters` (✅ implemented);
- discover MCP tool metadata during `build()` (✅ implemented);
- hide local-vs-MCP origin by presenting both as LangChain tools, while tracking
  the origin in `used_tools.provider` (✅ implemented);
- bind discovered MCP tools into LangGraph/LangChain tool-calling (✅ implemented);
- pass trusted request context through `ctx` into tool calls (⏳ planned —
  resolvers receive `ctx`, tools do not yet);
- redact secrets from traces (⏳ planned, task 0011);
- keep prompt wording out of the enforcement path.

Future per-tool access control should be added deliberately to the schema and
docs before implementation.

---

## Manual Smoke Test: Remote MCP (flagship example)

The flagship
[`examples/enterprise-knowledge-assistant/agents.yaml`](../examples/enterprise-knowledge-assistant/agents.yaml)
wires two real remote MCP servers — a public one (DeepWiki) and an authenticated
one (Context7). It demonstrates the intended user experience for remote MCP:
declare a server URL, grant an agent access with `agent.mcps`, and let the
platform create the generic MCP client automatically. No stdio configuration,
command/args, or per-server client code is needed.

These remote servers are used only as public/authenticated MCP examples for
validating runtime integration. They are not part of `make check`, and automated
tests do not call the real services.

The MCP declaration is URL-only:

```yaml
mcps:
  deepwiki:
    url: "https://mcp.deepwiki.com/mcp"
```

Validate (and inspect) the example offline, without contacting any server:

```bash
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml
agentctl inspect  examples/enterprise-knowledge-assistant/agents.yaml   # MCP url, tool_tags, transport
```

`inspect` prints each MCP server's `tool_tags` and the effective
`tool_tag_transport` (default header `X-MCP-Tool-Tag`, or an explicit override)
without connecting to any server.

Run the manual smoke test when provider dependencies and credentials are
available (copy `.env.example` to `.env` first):

```bash
agentctl run --config examples/enterprise-knowledge-assistant/agents.yaml \
  --env examples/enterprise-knowledge-assistant/.env \
  --message "Use DeepWiki to explain what the repository modelcontextprotocol/python-sdk is about."
```

To stream the final assistant answer as it is generated, add `--stream`.

The current sample model uses Anthropic via LangChain. To run it through Amazon
Bedrock instead, set a Bedrock model in YAML and configure AWS normally:

```yaml
model:
  provider: bedrock
  name: anthropic.claude-3-5-haiku-20241022-v1:0
  region: us-east-1
  temperature: 0.0
```

```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=my-profile
```

Environment credentials such as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
and `AWS_SESSION_TOKEN` also work through the standard AWS credential chain.
This DeepWiki call is a manual integration smoke test, not a unit test or CI
requirement; automated tests stay offline and deterministic.
