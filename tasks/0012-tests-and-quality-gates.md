# Task 0012 — Tests & Quality Gates

## Goal

Harden the overall quality gate: raise coverage of architecture-critical
behaviors, add an end-to-end pipeline test, and ensure `make check` is a
reliable gate (lint, types, tests) suitable for CI.

## Context

Tests accompany every prior task, but this task ensures the **whole pipeline**
(validate → compile → runtime → prompts → plugins → tools → trace) is covered and
that the quality gate is trustworthy.

**Read first:** `AGENTS.md`, `.ai/skills/testing.md`,
`docs/DEVELOPMENT_WORKFLOW.md`.

## Scope

- Add an end-to-end test exercising the full pipeline with fake resolver/access
  plugins and fake tools/MCP.
- Add tests for the binding architecture rules (lifecycle, access fail-closed,
  no rendered-prompt caching, redaction).
- Tighten `make check` and add a CI workflow that runs it.

## Files allowed to change

- `tests/**`
- `Makefile` (only to refine `check`)
- `.github/workflows/**` (new CI workflow)
- `pyproject.toml` (only for coverage/test config)

## Requirements

- One end-to-end test: spec → validate → compile → build engine once → invoke a
  request → assert response + trace, using fakes (no real network/LLM/secrets).
- Rule tests covering: engine-once / context-per-request, access fail-closed,
  rendered prompts not cached, secret redaction.
- `make check` runs format-check + lint (ruff) + types (mypy) + tests (pytest)
  and fails on any error.
- CI workflow runs `make install && make check` on push/PR.

## Out of scope

- New product features or layers.
- Real external integrations.

## Acceptance criteria

- [ ] End-to-end pipeline test passes using fakes only.
- [ ] Architecture-rule tests exist and pass.
- [ ] `make check` fails on lint/type/test errors (verified).
- [ ] CI workflow runs `make check` and is green.
- [ ] No secrets or live external calls in tests.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm the gate is reliable and the architecture
rules are covered by tests. Note the foundation→implementation arc is complete
through this task and recommend follow-on feature tasks.
