---
name: yaml-schema
description: Use when implementing YAML loading, schema models, or validation. Enforces declarative, typed, reference-validated specs with no hardcoded secrets.
---

# Skill: YAML Schema & Validation

## Purpose

Load `agent.yml` into typed models and validate it thoroughly, before any
compilation. Primary task: `tasks/0002-yaml-schema-and-validation.md`.

## When to Use

- Defining spec schema models, the loader, or validators.

## Files to Read First

- `skills/yaml-schema-skill.md` (root playbook).
- `docs/YAML_SPEC.md`,
  `docs/adr/0002-yaml-is-compiled-not-executed-directly.md`.

## Rules

- YAML is source specification only — never executed; no `eval` of spec values.
- Use typed models (Pydantic); reject unknown top-level keys; parse safely.
- Validate references: every hierarchy agent id exists in `definitions.agents`;
  referenced providers/tools/MCP servers exist.
- Validate prompt paths resolve; validate sidecar config; validate declared tool
  permissions.
- Validate no hardcoded secrets (only references like env var names).
- Support the two-part shape: `definitions` + indented `hierarchy` (single root
  matching `runtime.entrypoint`, no cycles).
- Validation happens before compilation; collect all errors, located clearly.

## Process

1. Define typed schema models per `docs/YAML_SPEC.md`.
2. Implement safe loading (file/string → models).
3. Implement semantic validation (references, hierarchy, secrets, sidecar).
4. Expose one entry point (e.g. `validate_spec(data) -> Result`); add tests.

## Checklist Before Finishing

- [ ] Typed models cover the spec; unknown keys rejected.
- [ ] References, hierarchy (single root, no cycles), prompt paths, sidecar, and
      tool permissions validated.
- [ ] No literal secrets accepted; safe parsing; all errors collected.
- [ ] Tests cover valid + each invalid case; `make check` passes.

## Common Mistakes to Avoid

- Mixing validation with compilation/runtime logic.
- Failing on the first error; allowing executable expressions or secrets.

## Expected Final Report

State the schema models added, what validations are enforced, the entry point,
test coverage of valid/invalid specs, and the `make check` result.
