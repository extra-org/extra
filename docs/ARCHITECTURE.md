# Architecture

This document describes the intended Agent Engine architecture. It is a design
blueprint; implementation is built task-by-task from [`tasks/`](../tasks/).

The current public contract is the YAML shape in
[`examples/agents.yml`](../examples/agents.yml), backed by
[`examples/config.schema.json`](../examples/config.schema.json).

---

## Product Vision

The engine turns a single declarative YAML file into a hierarchical multi-agent
system. A developer declares:

- system metadata and default model settings;
- MCP servers;
- Python plugin tools;
- Python plugin resolvers;
- orchestrators, which route to child nodes;
- agents, which execute work and may call tools/MCP servers;
- graph topology using indentation under `graph`.

At runtime, the engine receives a complete conversation, starts at the root
orchestrator, routes down the graph, renders prompt files with resolver values,
executes the chosen node, and returns a response plus trace.

Motivating shape:

```text
USER
  -> main_router
       -> flights_router
            -> domestic_flights_agent
            -> international_flights_agent
       -> super_agent
       -> admin_agent
```

---

## Core Principles

1. **Declarations first, topology second.** Flat sections declare reusable
   things. `graph` declares how they are connected.
2. **YAML is declarative data.** It is validated and compiled, never executed.
3. **RuntimeEngine is long-lived.** Build it once at startup from a compiled
   graph.
4. **ExecutionContext is per request.** Never store request state on the engine
   or compiled graph.
5. **The engine is stateless with respect to conversation.** Session history and
   memory are owned upstream; each invocation sends a complete conversation.
6. **Customer logic lives in plugins.** Auth, RBAC, database access, REST
   clients, and business context live in customer Python plugins, not in the
   generic engine.
7. **Prompt files are templates.** Parse/cache templates if useful, but render
   them per request.
8. **Prompt text is not a security boundary.** Access filtering and tool/data
   enforcement must happen outside prompt wording.

---

## Pipeline

```text
config.yml
  -> safe YAML load
  -> schema + semantic validation
  -> compile
  -> CompiledAgentGraph
  -> RuntimeEngine, created once
  -> ExecutionContext, created per request
  -> route graph
  -> render prompt templates
  -> execute orchestrator/agent
  -> response + trace
```

### Build / Validation / Compilation

Happens before serving requests:

- safely load YAML;
- validate against the JSON Schema and semantic rules;
- validate graph references, prompt paths, model overrides, resolver/tool/MCP
  references, protected-node access requirements, and no literal secrets;
- compile flat declarations plus `graph` topology into a typed
  `CompiledAgentGraph`;
- load reusable plugin instances and MCP clients later in the startup lifecycle.

The compiler should assign stable internal instance ids for each occurrence in
`graph`, because the same declared node id may appear in multiple places to model
DAG reachability.

### Runtime / Execution

Happens for each invocation:

- receive `POST /api/invoke` with headers and a complete `messages` array;
- create a fresh `ExecutionContext`;
- build `ctx` from request headers and request data;
- filter protected nodes with the fixed access plugin, if any;
- route from the root node through orchestrators using child descriptions and
  orchestrator prompts;
- call declared resolvers before a node runs;
- render prompt templates for this request;
- execute the selected agent or route through the selected orchestrator;
- expose configured tool and MCP capabilities to executor agents;
- record a trace.

### Customer Extension Layer

Customer extension code is Python plugin code loaded in-process. The uniform
shape is:

```python
class Resolvers:
    def __init__(self):
        ...

    def current_date(self, ctx):
        ...
```

The same class + method reference shape is used for resolvers and tools:

```yaml
resolvers:
  current_date:
    class: Resolvers
    method: current_date

tools:
  book_flight:
    class: FlightTools
    method: book_flight
```

Heavy integrations can live behind MCP servers, which may be implemented in any
language.

---

## Node Model

### Orchestrators

Orchestrators are routers. They have a `description`, an
`prompts.orchestrator` file, and optional `prompts.system`, `prompts.user`,
`model`, `resolvers`, and `protected`.

They route among their graph children. The routing criterion for a child is the
child's `description`, not separate routing code embedded in `graph`.

### Agents

Agents are executors. They have a `description`, optional prompt files,
optional resolvers, optional tools, optional MCP server references, optional
model override, and optional `protected`.

For the MVP, agents are normally leaves. If non-leaf agents are allowed later,
that should be an explicit schema and runtime decision.

---

## Access Control

Authorization is opt-in. Non-protected nodes are always reachable.

If any node has `protected: true`, the engine expects:

```text
plugins/access.py
```

with:

```python
class AccessResolver:
    def can_access(self, ctx, node_id: str) -> bool:
        ...
```

Before routing, the runtime calls `can_access(ctx, node_id)` for each protected
node. A true result keeps the node as a routing candidate. A false result or
exception hides the node from the router. `protected: true` without the access
plugin is a configuration error at startup.

The engine does not define roles, tenants, or auth token semantics. The customer
plugin interprets `ctx["headers"]["authorization"]` using the customer's
existing auth/RBAC system.

---

## Request Shape

The engine does not own conversation memory. Callers send the complete
conversation:

```http
POST /api/invoke
Authorization: Bearer <token>
Content-Type: application/json

{
  "messages": [{ "role": "user", "content": "I need groceries" }]
}
```

The runtime reroutes from the root every invocation. A future `start_at` or
similar optimization is possible, but not assumed by the current design.

---

## Layers

1. **Spec layer**: safe YAML loading and typed schema models.
2. **Validation layer**: JSON Schema plus semantic validation.
3. **Compiler layer**: normalized typed graph, bindings, and stable instance ids.
4. **Runtime layer**: long-lived engine and per-request execution contexts.
5. **Prompt rendering layer**: file templates rendered per request.
6. **Plugin layer**: resolver, tool, and access plugin loading/invocation.
7. **MCP/tool layer**: MCP client setup and tool execution.
8. **Observability layer**: trace routing, prompt, resolver, access, tool, and
   final response events.
9. **API/CLI layers**: validate, inspect graph, run locally, and serve.
10. **Deployment layer**: Docker/base-image packaging.

---

## First Implementation Target

The next implementation step is task `0002`: load and validate the current YAML
contract. It should use [`examples/agents.yml`](../examples/agents.yml) as a
valid fixture and [`examples/config.schema.json`](../examples/config.schema.json)
as the schema source of truth, then add semantic validation that JSON Schema
cannot express cleanly.
