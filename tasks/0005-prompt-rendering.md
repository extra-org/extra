# Task 0005 — Prompt Rendering

## Goal

Implement prompt **template** loading/caching and **per-request** rendering from
the `ExecutionContext`, with strict missing-variable errors. Wire it into the
runtime's prompt seam.

## Context

Prompts are templates; parsed templates are cached, rendered output never is.
Values are resolved per request. This task fills the prompt seam created in 0004.

**Read first:** `AGENTS.md`, `.ai/skills/prompt-rendering.md`,
`docs/PROMPT_RENDERING.md`,
`docs/adr/0004-prompts-are-templates-rendered-per-request.md`.

## Scope

- Implement template loading and a parsed-template cache.
- Implement strict per-request rendering against context values.
- Connect rendering to the runtime's prompt seam.

## Files allowed to change

- `src/agentplatform/prompts/**`
- `src/agentplatform/runtime/**` (only to connect the prompt seam)
- `tests/prompts/**`

## Requirements

- Parse templates once and cache by path + version; **never** cache rendered
  output.
- Render using only values from the `ExecutionContext`.
- Strict mode: missing/undeclared required variables raise a clear, named error.
- Templates are data, not code — no execution inside templates.
- No secrets read from or written to prompt files.

## Out of scope

- Sourcing context values from resolver plugins/DB/API (that is task 0006);
  here, render from whatever the `ExecutionContext` already holds.
- Tool execution, API/CLI.

## Acceptance criteria

- [ ] Parsed templates are cached; rendered prompts are not cached globally.
- [ ] Rendering pulls values from `ExecutionContext` per request.
- [ ] Missing required variable → clear, named error.
- [ ] No executable logic or secrets in templates.
- [ ] Prompt seam in the runtime uses this renderer.
- [ ] Tests cover render success, missing variable, and cache reuse.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm ADR 0004 (templates cached, rendered output
not; per-request rendering; loud failures). Recommend task 0006 next.
