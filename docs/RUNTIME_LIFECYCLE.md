# Runtime Lifecycle

This document defines the lifecycle of the runtime and is binding. The single
most important rule in this repository lives here:

> **`RuntimeEngine` is created once at application startup.
> `ExecutionContext` is created per request. Never create a runtime per
> request.**

→ See [ADR 0001](adr/0001-runtime-engine-created-once.md).

---

## Two lifecycles

### Startup (once per process)

```
load config.yml
  → validate
  → compile → CompiledAgentGraph (immutable)
  → construct RuntimeEngine(compiled_graph, providers, plugin registry, MCP manager, ...)
```

The `RuntimeEngine` and the `CompiledAgentGraph` are built **once** and shared,
read-only, across all requests. Expensive setup (parsing the graph, preparing
provider clients, loading and parsing prompt templates, opening MCP connections)
happens here — not on the request path.

MCP connections are explicit lifecycle work: `Engine.start()` asks the
engine-owned `MCPManager` to create one generic URL-based remote MCP client per
configured MCP server, initialize the sessions, and cache discovered tool
metadata. `Engine.stop()` closes those clients. `Engine.run()` does not
implicitly start MCP clients.

### Per request (many)

```
request arrives
  → create ExecutionContext(request_id, request data, trace)
  → filter protected nodes through access plugin
  → route through graph
  → resolve prompt variables through resolver plugins
  → render prompts (per request)
  → execute orchestrator/agent
  → call configured tool plugins/MCP servers as needed
  → finalize response + trace
```

Everything that varies between requests lives on the `ExecutionContext`.

---

## RuntimeEngine

- **Long-lived**, created at startup, one per process.
- **Holds shared, read-only collaborators**:
  - the `CompiledAgentGraph`
  - the prompt template loader (raw-template cache)
  - the prompt renderer
  - the resolver plugin registry
  - the access plugin adapter
  - the tool registry
  - the MCP manager
  - the LLM provider registry
  - observability/tracing infrastructure
  - runtime configuration
- **Holds NO request state.** No "current user", no "current request", no
  per-request mutable fields.
- **Thread/async-safe by construction**: because it carries no request state,
  many requests can use it concurrently.

What does **not** belong on `RuntimeEngine`:

- The current `user_id`, `tenant_id`, `customer_code`, identity, or permissions.
- The current request message.
- Rendered prompts for a specific request.
- The current trace path or tool results.
- Any mutable counter/buffer scoped to a single request.

## ExecutionContext

- **Per request**, created fresh, never reused.
- **Carries everything request-scoped**:
  - `request_id`
  - `message`
  - request headers and request data
  - optional identity/context derived by plugins
  - resolved context values
  - the selected node path
  - rendered prompt values
  - temporary tool results
  - trace events
  - errors
- **Passed explicitly** down through routing, context resolution, prompt
  rendering, plugin calls, and tool execution.
- **Discarded** when the request completes.

> **Simple rule:** `RuntimeEngine` = *how the system works*;
> `ExecutionContext` = *what is happening in this specific request*. Never store
> per-request state on `RuntimeEngine`.

---

## Why this matters

- **Correctness:** sharing request state on a long-lived engine causes data to
  leak between concurrent requests (e.g. one tenant seeing another's context).
- **Performance:** recompiling the graph or reconstructing the engine per
  request wastes the expensive work that should happen once.
- **Concurrency:** a stateless engine + per-request context is safe to run
  concurrently without locks around request data.

---

## Validation checklist (for any runtime change)

- [ ] `RuntimeEngine` construction happens only at startup paths (API/CLI boot),
      never inside a request handler.
- [ ] No request-scoped field is added to `RuntimeEngine` or
      `CompiledAgentGraph`.
- [ ] All request-scoped data lives on `ExecutionContext`.
- [ ] The compiled graph is treated as immutable at runtime.
- [ ] Tests cover concurrent requests not leaking state.
- [ ] `make check` passes.
