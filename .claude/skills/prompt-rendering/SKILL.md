---
name: prompt-rendering
description: Use when working on prompt templates or per-request rendering. Enforces cached templates, per-request values, and loud missing-variable errors.
---

# Skill: Prompt Rendering

## Purpose

Load prompt templates (cacheable) and render them per request from the
`ExecutionContext`, failing loudly on missing variables. Primary task: `0005`.

## When to Use

- Working on prompt template loading/caching or rendering.

## Files to Read First

- `skills/prompt-rendering-skill.md` (root playbook).
- `docs/PROMPT_RENDERING.md`,
  `docs/adr/0004-prompts-are-templates-rendered-per-request.md`.

## Rules

- Prompt files are templates; the parsed template may be cached (by path +
  version).
- Rendered prompts are created per request and never globally cached.
- Missing required variables fail with a clear, named error (no silent blanks).
- Dynamic values come from request, identity, sidecar, system time, memory,
  tool, DB/API — via the context resolver. YAML declares the source; the runtime
  resolves it.
- Templates are data, not code (no execution). Prompt text is not a security
  boundary.

## Process

1. Implement template loading + parsed-template cache.
2. Render strictly from `ExecutionContext` values per request.
3. Raise named errors for missing/undeclared variables.
4. Wire into the runtime's prompt seam; add tests (render, missing var, cache).

## Checklist Before Finishing

- [ ] Parsed templates cached; rendered output never globally cached.
- [ ] Missing required variable → clear named error.
- [ ] No logic/secrets in templates; values pulled from `ExecutionContext`.
- [ ] Tests cover success/missing/cache; `make check` passes.

## Common Mistakes to Avoid

- Caching rendered prompts (cross-request/tenant leak).
- Silent blanks for missing variables; logic/secrets in templates.
- Relying on prompt wording for enforcement.

## Expected Final Report

Confirm template caching vs. per-request rendering, loud failures, the values'
source, test coverage, and the `make check` result.
