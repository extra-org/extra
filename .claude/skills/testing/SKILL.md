---
name: testing
description: Use when writing or running tests. Enforces pytest, behavior-focused tests, mocked externals, and negative/security coverage.
---

# Skill: Testing

## Purpose

Write fast, deterministic, behavior-focused pytest tests that never touch real
external systems.

## When to Use

- Adding/changing any behavior (tests accompany behavior).
- Fixing a bug (add a regression test first).
- Working on the test suite or quality gate (task `0012`).

## Files to Read First

- `skills/testing-skill.md` (root playbook with categories + examples).
- `AGENTS.md`, `docs/DEVELOPMENT_WORKFLOW.md`, `pyproject.toml`.

## Rules

- Use `pytest`; tests live under `tests/` mirroring `src/agentplatform/`.
- Test behavior through public interfaces, not private implementation details.
- Mock LLMs, MCP servers, DBs, third-party APIs, and the sidecar.
- Never call real external services in unit tests.
- Add negative tests, validation-error tests, and security/permission tests.
- Test missing required prompt variables fail clearly; test sidecar
  allowed/denied flows when implemented; test `RuntimeEngine` is not recreated
  per request.
- Keep tests deterministic (control time/randomness/order).

## Process

1. Pick the category (unit / integration / contract / golden / negative /
   security).
2. Arrange with fixtures; parametrize input variations.
3. Mock external boundaries at the adapter seam.
4. Assert observable behavior/effects.
5. Run `make test` (and `make check`).

## Checklist Before Finishing

- [ ] New/changed behavior tested; bugs have regression tests.
- [ ] Public-interface, behavior assertions (not internals).
- [ ] Externals mocked; no real LLM/MCP/DB/API/sidecar calls.
- [ ] Negative + security cases covered.
- [ ] Deterministic; `make check` passes.

## Common Mistakes to Avoid

- Calling real external services in unit tests.
- Asserting on private attributes/log strings.
- Flaky tests; over-mocking until nothing is asserted.

## Expected Final Report

State which test files/categories were added, what behaviors and
negative/security cases are covered, the mocks/fakes used, the `make test` /
`make check` result, and any gaps left for later.
