# ADR 0007: The build/compile phase is separate from the runtime/execution phase

## Status

Accepted

## Context

The platform turns a declarative `agent.yml` into a running agent system. Two
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
   validate (schema, references, prompt paths, tools, MCP servers, hierarchy,
   reusable instances, resolver references, cycles, no hardcoded secrets), and
   compile into a `CompiledAgentGraph`. Later, optionally generate resolver stubs
   and deployment artifacts. **This phase does not execute user requests.**
2. **Runtime / execution phase** — per request: create an `ExecutionContext`,
   resolve identity/context (via resolvers/sidecar), route to an agent instance,
   render prompts, execute, enforce tool permissions and injected parameters, and
   return a response + trace.
3. **Client extension layer** — client-specific logic (auth, authorization,
   `customer_code`/tenant/permission lookups, DB/API calls, business context,
   tool input policies) lives in generated resolver functions, a client-owned
   sidecar, or plugins — **never** hardcoded into the generated runtime.

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
  (client logic in sidecar/extensions).

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
