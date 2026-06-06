---
name: architecture-review
description: Use when a change affects layers, lifecycles, or contracts. Enforces the project's architecture invariants and flags when an ADR is required.
---

# Skill: Architecture Review

## Purpose

Guard the project's architecture invariants and decide whether a change needs an
ADR.

## When to Use

- A change touches more than one layer or moves logic between layers.
- A change affects the YAML→graph pipeline, runtime lifecycle, prompts, the
  sidecar contract, or MCP/tool integration.
- Before approving anything that alters a public contract.

## Files to Read First

- `skills/architecture-review-skill.md` (root playbook).
- `AGENTS.md` §3–§4, `docs/ARCHITECTURE.md`, `docs/RUNTIME_LIFECYCLE.md`,
  all of `docs/adr/`.

## Rules (invariants)

- YAML is declarative; validate before compile; runtime never executes raw YAML.
- Validated config compiles into typed internal models (`CompiledAgentGraph`).
- `RuntimeEngine` created once; `ExecutionContext` per request; no request state
  on the engine/graph; graph immutable and reused.
- Prompt templates may be cached; rendered values resolved per request.
- Client-specific auth/context/business logic lives in the sidecar/plugin
  boundary.
- Prompt text is not security; tool permissions enforced outside prompts.
- Agents declare needs; the runtime resolves and enforces.
- MCP/tool integrations go through adapters.

## Process

1. Map the change to layers/contracts.
2. Verify pipeline direction (validate → compile → graph → runtime → ...).
3. Verify lifecycle (engine-once / context-per-request) and boundaries.
4. If a contract changes, require an ADR and assess compatibility.
5. Verdict: conforms / redesign / ADR needed.

## Checklist Before Finishing

- [ ] Every invariant upheld (or an ADR justifies a deviation).
- [ ] Correct layer; pipeline direction preserved; no improper coupling.
- [ ] Lifecycle respected; integrations via adapters.
- [ ] Contract changes have an ADR; `make check` passes.

## Common Mistakes to Avoid

- Runtime reading raw YAML or skipping compilation.
- Request state on long-lived objects; globally cached rendered prompts.
- Client logic in the runtime; direct MCP/LLM/DB calls from core logic.
- Silent contract changes without an ADR.

## Expected Final Report

State which layers/contracts are touched, how each relevant invariant holds (or
which ADR justifies deviation), lifecycle/boundary findings, whether an ADR is
required, and a clear verdict.
