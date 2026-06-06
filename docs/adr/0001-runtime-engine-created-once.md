# ADR 0001: RuntimeEngine is created once at startup

- **Status:** Accepted
- **Date:** Foundation phase
- **Related:** [RUNTIME_LIFECYCLE.md](../RUNTIME_LIFECYCLE.md),
  [ARCHITECTURE.md](../ARCHITECTURE.md)

## Context

The platform serves many requests against an agent system compiled from YAML.
Building that system (parsing the spec, compiling the graph, preparing provider
clients, loading prompt templates, opening MCP connections) is expensive and
deterministic. We must decide whether this setup happens once or per request,
and where request-specific state lives.

## Decision

The `RuntimeEngine` (and the `CompiledAgentGraph` it wraps) is constructed
**once at application startup** and is long-lived and shared, read-only, across
all requests. It holds **no** request-scoped state.

A fresh `ExecutionContext` is created **per request** and carries all
request-scoped data (identity, tenant, permissions, resolved context, routing
path, trace). It is passed explicitly through the request pipeline and discarded
when the request ends.

## Consequences

**Positive**

- Expensive setup happens once; the request path stays fast.
- A stateless engine is safe under concurrency without locking request data.
- Clear separation: shared/immutable on the engine, mutable/per-request on the
  context.

**Negative / constraints**

- Developers must never add request-scoped fields to `RuntimeEngine` or the
  compiled graph.
- The compiled graph must be treated as immutable at runtime.
- Reloading a changed YAML config requires building a new engine (e.g. at
  restart or via an explicit reload), not mutating the existing one.

## Alternatives considered

- **Runtime per request:** simplest mental model, but wastes the expensive
  compile/setup work and encourages stuffing request state into shared objects.
  Rejected.
- **Shared engine with mutable request fields:** causes cross-request data leaks
  under concurrency. Rejected.

## Enforcement

- `RuntimeEngine` construction only on startup paths (API/CLI boot).
- No request-scoped attributes on the engine or graph.
- Tests assert concurrent requests do not leak state.
