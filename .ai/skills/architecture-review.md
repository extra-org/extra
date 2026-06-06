---
name: architecture-review
description: Guard the project's architecture invariants. Use to evaluate any change affecting layers, boundaries, lifecycles, or contracts, and to decide whether an ADR is required.
---

# Skill: Architecture Review

## Purpose

Guard the project's architecture invariants. Use this to evaluate any change
that affects layers, boundaries, lifecycles, or contracts — and to decide
whether an ADR is required.

## When to Use This Skill

- A change touches more than one layer or moves logic between layers.
- A change affects the runtime lifecycle, the YAML→graph pipeline, prompts,
  plugin contracts, or tool/MCP integration.
- Reviewing whether a design respects the project's invariants.
- Before approving anything that alters a public contract.

## Files to Read First

- `AGENTS.md` §3 (non-negotiable rules) and §4 (layout).
- `docs/ARCHITECTURE.md` and `docs/RUNTIME_LIFECYCLE.md`.
- All ADRs in `docs/adr/`.
- The specific layer doc(s) the change touches.

## Core Principles (project architecture invariants)

These are binding. A change that violates one must be redesigned or justified by
a new/updated ADR.

- **YAML is declarative, not executable logic.** No code execution from spec
  values; routing conditions are declarative.
- **Validate before compile.** YAML is validated before it is compiled.
- **Runtime never executes raw YAML dictionaries.** It operates only on typed
  compiled models.
- **Validated config compiles into typed internal models** (`CompiledAgentGraph`).
- **`RuntimeEngine` is created once at startup**; the compiled graph is
  immutable and shared read-only.
- **`ExecutionContext` is created per request** and holds all request-scoped
  state. No request state on the engine/graph.
- **Prompt templates may be cached; rendered values are resolved per request.**
  No global cache of rendered prompts.
- **Client-specific auth/context/business logic lives in plugin
  boundary**, never in the runtime.
- **Tool permissions are enforced outside prompts**, at the tool/data layer;
  injected params can't be overridden by the model.
- **Agents declare needs; the runtime resolves and enforces** them.
- **Plugin contracts stay generic** — no client-specific fields baked into
  it.
- **MCP/tool integrations go through adapters**, not directly into core logic.

## Process

1. **Map the change to layers.** Identify which `src/agentplatform/<layer>`
   packages it touches and whether responsibilities stay separated.
2. **Check the pipeline direction.** Data flows validate → compile → graph →
   runtime → (prompts/context/tools). Flag backward/sideways coupling.
3. **Check lifecycles.** Engine-once vs. context-per-request; no request state
   on long-lived objects; graph immutable.
4. **Check boundaries.** No client/business logic in the runtime; external
   systems behind adapters; security enforced outside prompts.
5. **Check contracts.** If the YAML schema, plugin contracts, or API shape
   changes, require an ADR and assess compatibility.
6. **Decide.** Conforms / needs redesign / needs an ADR. Record reasoning.

## Checklist Before Finishing

- [ ] Each invariant in Core Principles is satisfied (or an ADR justifies a
      change).
- [ ] Change sits in the correct layer; boundaries intact.
- [ ] Pipeline direction preserved; no improper coupling.
- [ ] Engine-once / context-per-request lifecycle respected.
- [ ] No client-specific logic in the runtime; integrations via adapters.
- [ ] Security enforced outside prompts; injected params non-overridable.
- [ ] Contract changes have an ADR and a compatibility note.
- [ ] `make check` passes.

## Common Mistakes to Avoid

- Letting the runtime read raw YAML or skip compilation.
- Storing request state on `RuntimeEngine`/compiled graph for convenience.
- Caching rendered prompts globally.
- Slipping client auth/business rules into the runtime instead of plugins.
- Calling MCP/LLM/DB directly from core logic instead of through an adapter.
- Changing a contract silently without an ADR.

## Expected Final Report

State: which layers/contracts the change touches; how each relevant invariant is
upheld (or which ADR justifies a deviation); lifecycle/boundary findings;
whether an ADR is required; and a clear verdict (conforms / redesign / ADR
needed).
