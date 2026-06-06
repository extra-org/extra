---
name: senior-python-engineering
description: Use when writing or structuring Python code in src/agentplatform. Enforces small typed modules, clear boundaries, DI, and adapter protocols.
---

# Skill: Senior Python Engineering

## Purpose

Write Python like a senior engineer: small, typed, explicit, testable modules
with side effects at the edges — code future agents can safely modify.

## When to Use

- Implementing any Python in `src/agentplatform/` (tasks `0001`+).
- Designing a module, model, or adapter; choosing dataclass vs. Pydantic.

## Files to Read First

- `skills/senior-python-engineering-skill.md` (root playbook).
- `AGENTS.md` (layout), `docs/ARCHITECTURE.md`, `pyproject.toml`.

## Rules

- Small modules, single responsibility; no giant files.
- Typed models (`mypy` strict). Use **Pydantic** for validated/boundary data
  (YAML spec, sidecar/API payloads); **dataclasses** (often frozen) for internal
  domain models. Choose intentionally.
- Separate domain models from transport/API models.
- Keep side effects (I/O, network, time, randomness) at boundaries.
- Dependency injection over hidden globals; no mutable global request state.
- External integrations behind `typing.Protocol`/ABCs (so they can be faked).
- Explicit async boundaries; typed, actionable errors; small intent-named
  functions; avoid clever code and premature abstraction.

## Process

1. Locate the correct layer package.
2. Model data (Pydantic vs. dataclass) intentionally.
3. Define public interfaces + Protocols for dependencies first.
4. Implement pure core logic; keep I/O behind injected adapters.
5. `make format` → `make lint`; add tests; `make check`.

## Checklist Before Finishing

- [ ] Correct layer; small, single-purpose modules; fully typed.
- [ ] Pydantic/dataclass choice justified; domain vs. transport separated.
- [ ] Side effects at edges; deps injected; no global request state.
- [ ] Adapters behind Protocols; errors typed/actionable.
- [ ] No premature abstraction; `make check` passes.

## Common Mistakes to Avoid

- One module spanning multiple layers; `Any` to silence `mypy`.
- Hidden global state; concrete external clients hardwired into core logic.
- Building abstractions before a second real use case.

## Expected Final Report

State which modules changed and their layer, model choices and why, how deps are
injected/faked, that side effects are at edges, and the `make check` result.

Expected tools: `pytest`, `ruff`, `mypy` (or `pyright`), `pydantic`, `fastapi`
(API, task `0009`), `typer` (CLI, task `0008`).
