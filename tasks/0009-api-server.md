# Task 0009 — API Server

## Goal

Expose the runtime over HTTP: construct the `RuntimeEngine` **once at startup**
and create an `ExecutionContext` **per request**, returning a response + trace.

## Context

The API is a surface over the runtime. The most important rule: build the engine
once at application startup (lifespan/startup hook), never per request.

**Read first:** `AGENTS.md`, `.ai/skills/runtime-engine.md`,
`docs/RUNTIME_LIFECYCLE.md`, `docs/adr/0001-runtime-engine-created-once.md`,
`docs/ARCHITECTURE.md` (API layer).

## Scope

- Implement an HTTP server (e.g. FastAPI) that loads/validates/compiles the spec
  and builds the runtime at startup.
- Implement an invocation endpoint that creates an `ExecutionContext` per request
  and returns the response and trace.
- Include the plugin context/access entry point from task 0006.

## Files allowed to change

- `src/agentplatform/api/**`
- `pyproject.toml` (only to add the web framework + server deps)
- `tests/api/**`

## Requirements

- The `RuntimeEngine` is created exactly once, at startup, and reused across
  requests.
- Each request gets a fresh `ExecutionContext`; no request state on the engine.
- Protected-node access filtering runs before routing; auth/context behavior is
  delegated to customer plugins (no client-specific logic in the API).
- A health endpoint and an invoke endpoint at minimum.
- Errors return appropriate HTTP status codes; secrets are never returned.

## Out of scope

- Deployment/packaging (task 0010).
- Deep tracing/export (task 0011) beyond returning the basic trace.
- Implementing plugin/tool internals (reuse 0006/0007).

## Acceptance criteria

- [ ] Engine built once at startup; verified not rebuilt per request.
- [ ] Per-request `ExecutionContext`; no shared request state.
- [ ] Invoke endpoint returns response + trace; health endpoint works.
- [ ] Protected-node access delegates to the plugin layer.
- [ ] Error paths map to correct status codes; no secrets leaked.
- [ ] Tests cover startup-once, a successful invoke, and a denied request.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm ADR 0001 at the API boundary (engine once,
context per request). Recommend task 0010 next.
