# ADR 0007: The build/compile phase is separate from the runtime/execution phase

## Status

Accepted

## Context

The platform turns a declarative YAML config into a running agent system. Two
fundamentally different kinds of work are involved: deciding whether a system is
valid and building its structure (**build/compile**), and handling individual
user requests against that structure (**runtime/execution**). A third concern —
client-specific business logic — must not be baked into either.

If these phases are collapsed (e.g. validating/compiling on the request path, or
embedding client auth/business logic in the runtime), the system becomes slow,
unsafe under concurrency, hard to test, and impossible to reuse across clients.

## Decision

The project recognizes **three separated phases**, and they must not be collapsed:

1. **Build / generation / compilation phase** — before serving requests: load,
   validate (schema, references, prompt paths, tools, MCP servers, graph
   topology, reusable instances, resolver references, cycles, no hardcoded
   secrets), and compile into a `CompiledAgentGraph`. Later, optionally generate
   deployment artifacts. **This phase does not execute user requests.**
2. **Runtime / execution phase** — per request: create an `ExecutionContext`,
   filter protected nodes through the access plugin, resolve context through
   resolver plugins, route to an `AgentNode`, render prompts, execute, call
   tools/MCP as needed, and return a response + trace.
3. **Client extension layer** — client-specific logic (auth, authorization,
   DB/API calls, business context, and access decisions) lives in customer
   plugins — **never** hardcoded into the generated runtime.

## Consequences

- Validation/compilation errors are caught **before** serving traffic, with clear
  messages, not deep in request handling.
- The expensive build work happens **once**; the request path stays fast.
- The runtime stays **generic and reusable**; client specifics are isolated to
  extension boundaries and can change without editing runtime code.
- Build-phase tooling (`validate`, `graph`) and runtime tooling (`run`, `serve`)
  are naturally distinct CLI surfaces.
- This decision reinforces, and depends on,
  [ADR 0001](0001-runtime-engine-created-once.md) (engine built once),
  [ADR 0002](0002-yaml-is-compiled-not-executed-directly.md) (compile, don't
  execute YAML), and [ADR 0003](0003-client-specific-logic-lives-in-sidecar.md)
  (customer logic in plugins).

## Alternatives Considered

1. **Validate/compile lazily on the request path.** Rejected: pushes errors to
   request time, wastes work, and harms latency and concurrency.
2. **Embed client auth/business logic in the runtime.** Rejected: forces one
   client's model on everyone and breaks reusability (see ADR 0003).
3. **A single undifferentiated "engine" doing everything.** Rejected: collapses
   responsibilities, making the system hard to test, reason about, and extend.

## Related

- [ADR 0001](0001-runtime-engine-created-once.md),
  [ADR 0002](0002-yaml-is-compiled-not-executed-directly.md),
  [ADR 0003](0003-client-specific-logic-lives-in-sidecar.md)
- Docs: [ARCHITECTURE.md](../ARCHITECTURE.md#execution-phases-build-vs-runtime-vs-client-extension),
  [RUNTIME_LIFECYCLE.md](../RUNTIME_LIFECYCLE.md)

> This ADR records a **decision**. The build tooling, runtime, and extension
> mechanisms are **not implemented yet**; they are built by later tasks.
