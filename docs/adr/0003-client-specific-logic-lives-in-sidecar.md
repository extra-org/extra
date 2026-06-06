# ADR 0003: Client-specific logic lives in plugins

- **Status:** Accepted
- **Date:** Foundation phase
- **Related:** [SIDECAR_CONTEXT_AUTH.md](../SIDECAR_CONTEXT_AUTH.md),
  [ARCHITECTURE.md](../ARCHITECTURE.md)

## Context

Every customer authenticates differently, models users and permissions
differently, and stores business data differently. The engine must remain
generic while letting customers reuse their existing auth, RBAC, database, REST,
and service libraries.

## Decision

Client-specific authentication, authorization, identity resolution, database
lookups, REST calls, and business context live in customer Python plugins, not
in the engine.

The engine supports a uniform plugin shape: class instances are created once,
and methods receive request-scoped `ctx`.

- Resolver plugins fill prompt variables before a node runs.
- Tool plugins are exposed to the LLM at runtime.
- The fixed access plugin at `plugins/access.py` controls protected nodes via
  `AccessResolver.can_access(ctx, node_id) -> bool`.

The runtime is responsible only for loading plugins, calling configured methods,
mapping results into request execution, fail-closed access filtering, and
tracing. It contains no customer-specific logic.

## Consequences

**Positive**

- The runtime stays generic and reusable.
- Customers can adopt the engine with existing Python service libraries.
- There is one plugin shape to learn across resolvers, tools, and access.
- Access control can hide protected nodes from routers before routing.

**Negative / constraints**

- Customer plugin code runs in-process and shares the engine language/runtime.
- Plugin loading and dependency packaging need clear deployment support.
- Stronger isolation may require a future sidecar option.

## Alternatives Considered

- **Built-in auth/business modules in the runtime:** rejected because it bakes in
  one customer's model.
- **Sidecar-first extension:** useful for stronger isolation, but slower for MVP
  adoption. It may return later behind an explicit schema/ADR.

## Enforcement

- No customer-specific auth/business code in runtime modules.
- Protected access failures fail closed.
- Resolver/tool/access plugin calls are traced with secrets redacted.
