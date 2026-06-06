---
name: documentation
description: Use when editing docs, README, AGENTS.md, or ADRs. Keeps documentation honest and synchronized with the code.
---

# Skill: Documentation

## Purpose

Keep documentation accurate, honest, and synchronized with the code.

## When to Use

- Editing `docs/`, `README.md`, `AGENTS.md`, or `CLAUDE.md`.
- Adding/changing an ADR.
- Shipping a behavior/contract change docs must reflect.

## Files to Read First

- `skills/documentation-skill.md` (root playbook).
- `docs/README.md` (index/reading order), the doc/ADR being changed.

## Rules

- Be honest about implemented vs. planned features; use accurate status markers.
- Never claim unimplemented features work (this is the foundation phase).
- Prefer flows, diagrams, and examples where they clarify.
- Update docs in the same change as the behavior they describe.
- Record architectural decisions as ADRs, not buried prose.
- Right scope: README = user-facing; AGENTS.md = repo-wide agent rules; ADRs =
  architectural decisions; `docs/` = design.

## Process

1. Choose the correct file(s) for the scope; avoid scattering content.
2. Write concisely; add an example/flow if helpful.
3. For a binding decision, add `docs/adr/NNNN-title.md` in the existing format.
4. Sync indexes/cross-references.
5. Re-read for honesty; run `make check`.

## Checklist Before Finishing

- [ ] No unimplemented feature claimed as working; status markers accurate.
- [ ] Docs updated with the behavior; decisions captured in an ADR.
- [ ] Correct file for the scope; examples/flows where useful.
- [ ] Cross-references/indexes updated; `make check` passes.

## Common Mistakes to Avoid

- Documenting aspirational behavior as real.
- Changing a contract in prose without an ADR.
- Letting README/AGENTS/docs drift out of sync; duplicating content.

## Expected Final Report

State which docs/ADRs changed and why, confirmation nothing unimplemented is
claimed as working, any new ADR, which indexes were updated, and the
`make check` result.
