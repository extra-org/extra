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
stops requesting tools. Each call is recorded in the run's tool-usage repository
with its `provider` (`"local"` or `"mcp"`), so the origin is tracked for tracing
even though it is hidden from the model.

### Normalized tool results

A successful tool call is represented inside Extra as a provider-independent
`NormalizedToolResult`, not as only a string:

```python
NormalizedToolResult(
    text="Found 2 invoices",
    structured={"count": 2},
    artifact={"source": "billing"},
)
```

The fields have separate responsibilities:

- `text` is the existing model-facing result. MCP text blocks are joined with
  deterministic normalization rules. A structured-only result uses canonical
  JSON text; a completely empty result uses a stable placeholder.
- `structured` is machine-readable output, including MCP
  `structuredContent`. It must be JSON-like and is not copied into a model
  message as metadata. For a structured-only result, its canonical JSON is
  deliberately used as the model-facing `text` fallback. The shared JSON-safe
  value policy limits nesting to 64 levels, total values to 10,000, cumulative
  string data to 1,000,000 UTF-8 bytes, individual keys to 1,024 bytes,
  cumulative key data to 256,000 bytes, and final canonical JSON to 1 MiB.
- `artifact` carries bounded, relevant artifact metadata. Raw in-memory binary
  bodies and oversized values are omitted and replaced by type/size metadata.
  The adapter limits depth to 8, each collection to 128 entries, total visited
  values to 1,024, individual strings to 8,192 characters, cumulative string
  content to 32,768 characters, keys to 256 characters, and final canonical
  metadata to 65,536 characters.

`langchain-mcp-adapters` currently exposes MCP `structuredContent` through the
`ToolMessage.artifact.structured_content` path. Extra reads that provider
contract once at the tool adapter boundary and requires version 0.2 or newer,
where that artifact contract is available. It passes only its own normalized
result deeper into the runtime. Local dictionary/list results are normalized by
the same abstraction; plain string tools remain unchanged.

Only `text` is appended to the LangChain conversation. The complete normalized
result is available to trusted result hooks and is serialized into the
idempotency ledger as a versioned, JSON-primitive payload. A replay therefore
restores the same text, structured value, and artifact metadata without calling
the provider again. None of the non-text values are added to tool-usage records,
logs, model messages, callbacks, traces, or errors automatically.
The structured-only text fallback is the explicit exception: because that JSON
becomes model text, it is visible wherever ordinary model messages are visible.

The ledger's atomic claim has one execution owner. Concurrent duplicate callers
wait for that owner and replay its immutable terminal result; terminal rows
cannot be overwritten. Legacy string ledger values restore as text-only
results. Custom repository adapters implement the same claim/wait/complete
contract and must durably serialize the versioned primitive payload.

Structured values are preserved without application-specific schema validation,
but the runtime enforces its generic JSON-safe shape. A malformed provider
result becomes a controlled failed tool result and is never converted with
`repr()` or an arbitrary `str()` fallback. See
[`ADR 0004`](adr/0004-normalized-structured-tool-results.md).

The engine is driven as an async context manager: `build()` connects MCP servers
and discovers tools; `close()` (on context exit) releases them. `run()` does not
connect MCP servers on its own — `build()` must run first.

```python
async with LangGraphEngine(base_dir) as engine:
    await engine.build(spec)   # connects MCP servers, discovers tools
    result = await engine.run(message)
```

## Shared Tool Usage

Every run collects deterministic tool-usage records on the runtime
tool-execution path — in call order, not inferred from the final answer or
requested from the model. The **source of truth is a repository**, not the
LangGraph state:

```text
ToolInvoker            (coordinates one tool call)
      │
      ▼
ToolUsageTracker       (execution event → domain record)
      │
      ▼
ToolUsageRepository    (source of truth: run → agent → tool call)
      │
      ├──────────────► ToolUsageContextProvider ──► private model context
      │
      └──────────────► run trace (RunResult.used_tools)
```

Because every agent shares one repository instance, an agent that starts later
sees what earlier agents did — including agents nested under an orchestrator —
without any of it being threaded through `GraphState`. Orchestrators receive the
same context, so a supervisor can answer a user asking what has already been
done.

### Persistence model

The domain relationship is `conversation → run → agent → tool call`. One record
per **logical invocation**, identified by `(run_id, tool_call_id)`:

| Field | Meaning |
| --- | --- |
| `conversation_id` | The conversation the run belongs to (absent outside one) |
| `run_id` | The exact run the invocation happened in |
| `agent_id` | The agent that invoked it — the id the approval subsystem also records, and the name the model is shown |
| `agent_path` | That agent's position in the graph (`openwebui/admin_management/user_management`) |
| `tool_call_id` | Stable id of the logical invocation, unchanged across suspend/resume |
| `tool_name` / `provider` / `server_id` | What was called, and where it came from |
| `kind` | `tool` for a real tool/MCP call, `agent` when an orchestrator delegated to a child |
| `status` | `succeeded`, `failed`, or `denied` |
| `error` | Bounded error text for a failed call |

Conversation scope gives continuity across the user's turns; run scope keeps
execution-level traceability. Neither replaces the other, and both are queryable.

Delegating to a child agent is an action too, so an orchestrator records it —
that is how a later turn can reconstruct the routing that already happened.
Those records are `kind = agent`; the caller-facing run trace reports
`kind = tool` only, while private model context renders tools and delegations in
separate labelled sections (see below).

`record` is an upsert on that identity, so a graph re-entry after an approval
resume updates the existing record instead of adding a second one. Arguments and
results are never stored: they may carry sensitive or oversized data, and no
consumer of tool usage needs them.

This tool-usage repository is distinct from the private tool-execution
idempotency ledger. Tool usage stores no result values; the execution ledger
stores `NormalizedToolResult` so a replay can reproduce the completed call.

`ToolUsageRepository` is an abstract base class with three operations (`record`,
`list_for_run`, `list_for_conversation`). The engine ships a process-local adapter
(`InMemoryToolUsageRepository`); a distributed deployment supplies a Redis- or
PostgreSQL-backed adapter with the same contract and injects it at the
composition root (`build_tool_usage_repository` in `agent_manager/composition.py`,
or the `tool_usage_repository=` argument of `LangGraphEngine`). No agent, node,
tool-loop, or model-context code changes when the backend changes.

Both list operations accept an optional invocation-kind filter and positive
`limit`. A bounded read returns the newest matching records in chronological
order. Context projections request `limit + 1`, using the extra row only to mark
the output as truncated; filtering before limiting prevents delegation-heavy
histories from under-filling tool-only reports.

Recording is observability, not part of the tool's contract: a repository write
failure is logged at WARNING with the invocation's identity and swallowed, so a
metadata problem can never turn a completed tool call into a failed one. The
visible cost is a trace that may be missing an entry the log names explicitly.

### Model context is a projection, not the record

`ToolUsageContextProvider` reads the records for a `ToolUsageScope` and projects
them into a small, private block supplied to the model as a **system-role
message** next to the system prompt:

```text
## Execution record for this conversation
Internal execution metadata, not chat history. Some of the tools bound to you are
other agents: calling one delegates the work, it does not perform it. ...

### Tools executed
...
user_management:
- create_user [succeeded]

### Agents delegated to
...
- openwebui -> admin_management
```

An orchestrator's children are bound to it as callable tools, so their
descriptions name them as delegations (`Delegate this request to the 'x' agent`)
and its instruction contract states that calling one hands the work over rather
than performing it. The record's two sections exist for the same reason. Asked which tools it had run, one answered `admin_management` — the child
it had merely routed to — and defended it, because from its own binding that is
a tool. Naming only the executed tools did not help either: the model trusted its
tool list over an unexplained record. So the record names both levels and states
the difference, and successful routing carries no `[succeeded]` marker that could
read as a tool result. Pass `include_delegations=False` to report executed tools
only.

Only agent, tool name, and status cross that boundary — no timestamps, ids,
arguments, results, or error text. The block is never a user or assistant turn,
so it does not enter the conversation history the caller persists and never
reaches the user; a conversation with no tool usage adds nothing at all.

Two policies live in the provider, and changing them touches nothing else:

* **Scope** — the conversation is preferred when the caller has one, so a
  follow-up turn (a new `run_id`) still knows what earlier turns did; a run
  outside any conversation falls back to its own run.
* **Size** — the most recent 50 invocations, bounding prompt growth on a long
  conversation. A trimmed record says so, rather than passing itself off as
  complete. The provider requests only `limit + 1` matching records, so one
  extra row detects truncation without loading the full history.

The repository remains the source of truth even when a tool result is also
present in the current model turn. Records are never hidden by tool name: two
calls to the same tool can be different logical invocations, and an orchestrator
can legitimately delegate to the same child more than once.

The block is re-read **before every model turn** (`ModelContext` keeps one slot
for it next to the system prompt), so a model invocation always reflects tools
that finished since the previous one — including during the same turn. Every
participant receives it: leaf agents, intermediate orchestrators, and the root.

This supplements the standard tool protocol; it does not replace it. An agent's
own active call still reaches its model as `AIMessage(tool_calls)` →
`ToolMessage(result)` → model, unchanged. The shared context solves the other
problem: awareness across agents, across runs, and across the conversation.

### Answering "which tools have run?"

Reporting is a separate problem from reasoning, and it needed a separate
mechanism. An orchestrator's children are bound to it as tools, so asked which
tools had run it named the child agent it had delegated to — and kept doing so
through four rounds of instruction and record wording, because from its own tool
list that answer is true.

Orchestrators are therefore given a read-only engine tool,
`list_executed_tools`, which returns the conversation's executed tools from the
repository:

```text
Tools executed in this conversation, oldest first:
- user_management: add_new_user [succeeded]
```

The passive context remains useful for reasoning, but it is not enough for an
explicit reporting question: the model can still classify a bound child agent
as a tool despite the labelled record. Keeping this reporting tool trades one
reserved engine-tool name and an extra model tool call for a deterministic
repository lookup with a clear result boundary. The answer now comes from a
tool result the model cannot contradict from its bindings, rather than from an
instruction it can ignore. The two read paths
degrade differently on purpose: the passive block falls back to empty context
when the repository cannot be read, while the report says the record is
unavailable — claiming "nothing has run" would invite an agent to repeat an
action that already took effect. The tool performs no
side effect, is not counted against child-agent call limits, and is not itself
recorded as usage. It changes nothing about Human-in-the-Loop: orchestrator-level
tools have never passed the approval gate — that gate belongs to the real tools
an agent executes, which still all pass through it. A configured child named
`list_executed_tools` wins, and the engine's tool is not bound.

### Run trace

`RunResult.used_tools` is a second projection of the same records — the
caller-facing trace returned by the API and rendered by the widget, including
tools called by nested agents, and scoped to the run. It reports real tool/MCP
calls only; the route an orchestrator took is reported separately as `visited`.

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

Every logical tool call is printed in call order.

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
  the origin in the tool-usage records (✅ implemented);
- bind discovered MCP tools into LangGraph/LangChain tool-calling (✅ implemented);
- reach the run's context from a tool via `current_run_context` (✅ implemented —
  a context variable rather than a `ctx` parameter, so tool signatures stay
  exactly what the model sees);
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
