---
name: refactoring
description: Use when restructuring code without changing behavior. Enforces small, scoped, test-backed steps that preserve layers and contracts.
---

# Skill: Refactoring

## Purpose

Change the shape of code without changing its behavior, safely and in small
steps, preserving layers and public contracts.

## When to Use

- Improving structure/naming/readability of existing code.
- Extracting a function/module, splitting a large file, removing duplication.
- Reducing complexity flagged in review.

## Files to Read First

- `skills/refactoring-skill.md` (root playbook).
- `AGENTS.md` (no large uncontrolled rewrites), the tests covering the code,
  any doc/ADR for the contracts involved.

## Rules

- Do not refactor unrelated code; stay in scope.
- Preserve public behavior and contracts.
- Add/confirm tests before risky changes.
- Refactor in small steps; keep commits logically scoped.
- Do not collapse layers; do not introduce circular dependencies.
- Update docs/ADRs/tests when a concept is renamed or moved.
- Prefer simple extraction over complex patterns.

## Process

1. Define the goal and the in-scope files.
2. Ensure a test safety net (add characterization tests if thin).
3. Refactor in small steps; run `make test` after each.
4. Keep layers intact; update renamed concepts everywhere.
5. Final `make check`; diff should be structure-only.

## Checklist Before Finishing

- [ ] Behavior/contracts unchanged; tests still pass.
- [ ] Safety net existed/was added; change is scoped.
- [ ] No layer collapse; no import cycles.
- [ ] Renames updated across code/tests/docs; `make check` passes.

## Common Mistakes to Avoid

- "While I'm here" scope creep; refactoring without tests.
- One giant commit mixing behavior + restructuring.
- Renaming in code but not in docs/ADRs; introducing cycles.

## Expected Final Report

State the goal and scope, the safety net used, the small steps taken,
confirmation behavior/contracts/layers are intact, any renamed concepts and
where updated, and the `make check` result.
