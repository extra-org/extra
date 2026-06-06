# Task 0004 — Runtime Engine

## Goal

Implement the long-lived `RuntimeEngine` and per-request `ExecutionContext`, plus
basic routing through the compiled graph and a minimal recursive execution
skeleton. **No real LLM calls, prompts, plugins, or tools yet** — those are wired
in by later tasks behind clear seams.

## Context

This is the heart of the architecture. The engine is built once at startup from
the `CompiledAgentGraph`; a fresh context is created per request. This task sets
the lifecycle and seams that prompts (0005), plugin context/access (0006), and
tools (0007) plug into.

**Read first:** `AGENTS.md`, `.ai/skills/runtime-engine.md`,
`docs/RUNTIME_LIFECYCLE.md`,
`docs/adr/0001-runtime-engine-created-once.md`.

## Scope

- Implement `RuntimeEngine`, constructed from a `CompiledAgentGraph`, holding
  only shared, read-only collaborators.
- Implement `ExecutionContext`, created per request, carrying request-scoped
  state and a trace accumulator.
- Implement routing from the root through the graph and a recursive
  execution skeleton that returns a response + trace.
- Define extension seams (interfaces) for prompt rendering, plugin access/context, and
  tool execution — without implementing those subsystems.

## Files allowed to change

- `src/agentplatform/runtime/**`
- `tests/runtime/**`
- Minimal seam interfaces may live in the relevant layer packages
  (`prompts`, `context`, `tools`) as protocols/abstract types only.

## Requirements

- `RuntimeEngine` holds **no** request state; the compiled graph is treated as
  immutable.
- `ExecutionContext` holds all request-scoped data (`request_id`, request data,
  resolved context placeholder, trace).
- The engine is safe to use concurrently (no shared mutable request state).
- Routing selects a node path deterministically from declarative metadata.
- Execution operates on compiled node instances, not raw declarations; trace
  events use `instance_id` and also include `node_id`.
- Execution is recursive over the graph and produces a response + trace
  object, even if downstream subsystems are stubbed at the seam.
- Seams are abstract (protocols/interfaces); no concrete prompt/plugin/tool
  logic here.

## Out of scope

- Prompt rendering implementation (0005).
- Plugin context/access implementation (0006).
- Tool/MCP implementation and enforcement (0007).
- API/CLI surfaces (0008, 0009).

## Acceptance criteria

- [ ] `RuntimeEngine` is constructed once and carries no request state.
- [ ] `ExecutionContext` is per-request and holds all request-scoped data.
- [ ] Routing traverses the compiled graph from the root.
- [ ] A request produces a response + trace through the execution skeleton.
- [ ] Seams for prompts/plugins/tools exist as interfaces only.
- [ ] A test asserts concurrent requests do not leak state.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm ADR 0001 is respected (engine once, context
per request, no shared request state). Recommend task 0005 next.
