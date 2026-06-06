---
name: code-review
description: Use when reviewing a diff/PR or self-reviewing before finishing. Produces a structured senior-level review focused on architecture, boundaries, security, and tests.
---

# Skill: Code Review

## Purpose

Perform senior-level code review for this agent platform: architecture and
boundaries first, then correctness, security, testability, and simplicity.

## When to Use

- Reviewing a PR/diff or another agent's change.
- Self-reviewing your own change before declaring a task done.

## Files to Read First

- `skills/code-review-skill.md` (the in-depth root playbook — read this).
- `AGENTS.md` (§3 architecture rules), `CLAUDE.md`.
- `docs/ARCHITECTURE.md` + the layer doc(s) the change touches.

## Rules

- Review architecture before syntax; check separation of concerns.
- Validation, compilation, runtime, prompt rendering, sidecar, and tools stay
  separated.
- No request state on `RuntimeEngine`/compiled graph (request state lives on
  `ExecutionContext`).
- Security enforced outside prompts; tool permissions/injected params enforced
  at the tool layer; no hardcoded secrets.
- Public interfaces clean/typed; errors actionable; code testable and as simple
  as possible; no unnecessary abstractions. Check backward compatibility if a
  contract changed (require an ADR).

## Process

1. Confirm the change is in scope for its task.
2. Check layer placement and boundaries.
3. Check lifecycle/state separation.
4. Check security (enforcement, secrets, redaction).
5. Check tests cover behavior + negatives, not internals.
6. Write the structured report.

## Checklist Before Finishing

- [ ] Correct layer; boundaries intact; no request state on long-lived objects.
- [ ] Runtime uses compiled models, not raw YAML.
- [ ] Security enforced outside prompts; no secrets; secrets redacted.
- [ ] Behavior + negative tests present; code testable and simple.
- [ ] Contract changes have an ADR.

## Common Mistakes to Avoid

- Nitpicking style while missing a boundary/lifecycle violation.
- Accepting prompt text as a security control.
- Approving tests that assert private internals.

## Expected Final Report

1. Summary 2. Blocking issues 3. Non-blocking issues 4. Architecture concerns
5. Security concerns 6. Testing gaps 7. Suggested improvements
8. Final recommendation (Approve / Approve with changes / Request changes).
