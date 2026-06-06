---
name: runtime-engine
description: Use when working on RuntimeEngine, ExecutionContext, routing, or the request lifecycle. Enforces engine-once / context-per-request.
---

# Skill: Runtime Engine

## Purpose

Implement the long-lived `RuntimeEngine` and per-request `ExecutionContext` with
clean routing and testable execution. Primary tasks: `0003`, `0004`.

## When to Use

- Working on `RuntimeEngine`, `ExecutionContext`, routing, or execution.

## Files to Read First

- `skills/runtime-engine-skill.md` (root playbook).
- `docs/RUNTIME_LIFECYCLE.md`,
  `docs/adr/0001-runtime-engine-created-once.md`.

## Rules

- `RuntimeEngine` is long-lived, created once at startup from the compiled graph.
- `ExecutionContext` is created per request and holds all request-scoped state.
- No request state on `RuntimeEngine` (no current user/request/rendered prompt).
- Reuse the immutable compiled graph; never recompile or rebuild per request.
- Resolve prompt values per request (via the context resolver/sidecar).
- Keep routing, execution, and adapters cleanly separated and testable.

## Process

1. Build the engine only on startup paths from `CompiledAgentGraph`.
2. Put per-request data on a fresh `ExecutionContext`, passed explicitly.
3. Implement routing from the entrypoint through the hierarchy.
4. Keep prompt/sidecar/tool integration behind seams (Protocols).
5. Add a concurrency test (no state leak); `make check`.

## Checklist Before Finishing

- [ ] Engine constructed once; no request state on engine/graph.
- [ ] All per-request data on `ExecutionContext`; graph immutable + reused.
- [ ] Routing traverses the compiled hierarchy; seams are interfaces.
- [ ] Concurrent-request leak test; `make check` passes.

## Common Mistakes to Avoid

- Recreating the runtime / recompiling the graph per request.
- Storing "current user/request" on the engine; mutating the graph.

## Expected Final Report

Confirm engine-once/context-per-request, describe routing/execution, note the
concurrency test, and give the `make check` result.
