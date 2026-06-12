# YAML Specification

This document describes the declarative Agent Engine configuration. The schema
source for the current example is [`examples/config.schema.json`](../examples/config.schema.json),
and the reference sample is [`examples/agents.yml`](../examples/agents.yml).
Validation and schema models are implemented; compilation is planned for task
`0003`.

The YAML has two conceptual halves:

- **Flat declarations** describe what exists: MCP servers, plugin tools,
  resolvers, orchestrators, and executor agents.
- **`graph` topology** describes how those declared nodes are connected at
  runtime. Indentation is the topology.

The runtime never executes raw YAML. The platform validates this file into typed
spec models first. Later tasks compile validated specs into a typed graph and
run requests against that graph.

---

## Conceptual Shape

```yaml
system:
  name: "Rami Levy AI System"

defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.7

mcps:
  flights_mcp:
    url: "https://company.com/mcp/flights/sse"

tools:
  book_flight:
    class: FlightTools
    method: book_flight

resolvers:
  current_date:
    scope: shared
    return_type: str

orchestrators:
  main_router:
    description: "Routes the user by topic."
    prompts:
      orchestrator: "prompts/main_router/orchestrator.md"
      system: "prompts/main_router/system.md"

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    prompts:
      system: "prompts/domestic_flights/system.md"
      user: "prompts/domestic_flights/user.md"
    resolvers: [current_date]
    tools: [book_flight]
    mcps: [flights_mcp]

graph:
  main_router:
    domestic_flights_agent:
```

---

## Top-Level Keys

| Key             | Required | Purpose |
| --------------- | -------- | ------- |
| `system`        | yes      | Human-readable system metadata. |
| `defaults`      | no       | System-wide defaults, currently `defaults.model`. |
| `mcps`          | no       | MCP server declarations keyed by id. |
| `tools`         | no       | Python plugin tools exposed to LLM agents. |
| `resolvers`     | no       | Deterministic prompt-variable resolvers. |
| `orchestrators` | no       | Router nodes. |
| `agents`        | no       | Executor nodes. |
| `graph`         | yes      | Runtime topology, with one root entrypoint. |

Unknown top-level keys are rejected. Secrets must not appear in YAML.

---

## Node Types

There are two declared node types. They are separate for developer clarity even
if the compiler stores them in one internal node model.

**Orchestrators** are routers. They choose among their children using the
children's `description` fields plus the orchestrator prompt. They do not own
tools or MCP servers; their children are their capabilities.

```yaml
orchestrators:
  flights_router:
    name: "Flights Router"
    description: "Routes flight requests to domestic or international handling."
    model:
      provider: anthropic
      name: claude-haiku-4-5
      temperature: 0.0
    prompts:
      orchestrator: "prompts/flights_router/orchestrator.md"
      system: "prompts/flights_router/system.md"
```

**Agents** are executors. They run an LLM with prompt files and may call tools
or MCP servers.

```yaml
agents:
  super_agent:
    name: "Supermarket"
    description: "Handle supermarket orders and cart operations."
    prompts:
      system: "prompts/super/system.md"
    resolvers: [user_name, subscription]
    tools: [add_to_cart]
    mcps: [super_mcp]
```

Every id referenced in `graph`, `resolvers`, `tools`, or `mcps` must be declared
in the corresponding top-level section.

---

## Prompts

Prompt text lives in files, not inline YAML. Each node uses a `prompts:` object:

| Field | Applies to | Required |
| ----- | ---------- | -------- |
| `orchestrator` | orchestrators only | yes |
| `system` | orchestrators and agents | no |
| `user` | orchestrators and agents | no |

All prompt files may contain `{{variables}}`. Variables are resolved per request
by declared resolvers before the node runs. Parsed templates may be cached;
rendered prompts are never cached globally.

Recommended layout:

```text
prompts/
  main_router/
    orchestrator.md
    system.md
  domestic_flights/
    system.md
    user.md
```

---

## Resolvers vs. Tools

Resolvers and tools both point at Python plugin methods, but they run at
different times and have different trust boundaries.

| | Resolver | Tool |
| --- | --- | --- |
| Runs | Before the node runs | During LLM execution |
| Chosen by | Engine | LLM |
| Exposed to LLM | No | Yes |
| Token cost | None | Yes |
| Purpose | Fill `{{variables}}` | Perform actions |

Resolvers are declared as ids in YAML and implemented as methods on the
agent-specific class configured in `plugins/resolvers/resolvers.toml`:

```yaml
resolvers:
  current_date:
    scope: shared
    return_type: str
```

```toml
[resolvers]
base_class = "plugins.resolvers.base.BaseResolver"

[resolvers.agents.domestic_flights_agent]
class = "plugins.resolvers.domestic_flights_agent.DomesticFlightsAgentResolver"
```

The engine loads the selected agent's customer resolver class once. Methods
receive `ctx`, which the engine builds from request headers and request data.
Shared resolver methods are generated on `BaseResolver` and inherited by agent
resolver classes; agent-scoped resolver methods are generated only on the
relevant child class.

---

## Model Configuration

`defaults.model` sets the default model for every node. A node may define its own
`model`, but a node-level model is a **full replacement**, not a field-level
merge. If a node overrides the model, it must provide all required model fields.

---

## Graph Topology

`graph` is a nested mapping. The single top-level key is the root entrypoint.
Every key must reference an id declared under `orchestrators` or `agents`.

```yaml
graph:
  main_router:
    flights_router:
      domestic_flights_agent:
      international_flights_agent:
    super_agent:
    admin_agent:
```

A `null` value means leaf node. YAML also represents an empty mapping key like
`super_agent:` as `null`, so leaves may be written naturally.

Semantic validation must enforce:

- exactly one root entrypoint;
- every graph id exists in `orchestrators` or `agents`;
- orchestrators may have children;
- agents are normally leaves for the MVP;
- repeated ids are allowed to model DAG reachability, but the compiler must
  assign stable node paths for each occurrence;
- cycles are rejected.

---

## Access Control

Authorization is opt-in per node:

```yaml
agents:
  admin_agent:
    description: "Sensitive administrative operations."
    protected: true
    prompts:
      system: "prompts/admin/system.md"
```

If any node has `protected: true`, the engine expects a fixed plugin contract at
`plugins/access.py`:

```python
class AccessResolver:
    def can_access(self, ctx, node_id: str) -> bool:
        ...
```

Before routing, protected nodes are checked. Allowed nodes remain candidates;
denied nodes are hidden from the router entirely. Non-protected nodes are never
checked. Access failures fail closed. `protected: true` without the access plugin
is a configuration error at startup.

---

## Request Shape

The engine is stateless with respect to conversation. An upstream application
owns sessions and memory, then invokes the engine with a complete conversation:

```http
POST /api/invoke
Authorization: Bearer <token>
Content-Type: application/json

{
  "messages": [{ "role": "user", "content": "Book me a flight to Eilat" }]
}
```

The engine passes headers and request data into `ctx`; customer plugin code
interprets auth tokens and business identity.

---

## Validator Rules For Task 0002

- Parse YAML safely; never execute YAML content.
- Reject unknown keys according to `examples/config.schema.json`.
- Enforce one root in `graph`.
- Enforce graph references to declared node ids.
- Enforce resolver/tool/MCP references.
- Enforce orchestrator `prompts.orchestrator`.
- Enforce full-replacement model overrides.
- Enforce access plugin requirements when `protected: true` exists.
- Reject literal secrets in config and prompt paths.
- Collect all validation errors with useful locations.

→ See [ADR 0002](adr/0002-yaml-is-compiled-not-executed-directly.md).
