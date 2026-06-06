# Task 0002 — YAML Schema & Validation

## Goal

Load an Agent Engine YAML config into typed schema models and validate it
thoroughly, producing either a validated spec object or a structured set of
errors. **No compilation or runtime behavior.**

## Context

The current YAML contract is demonstrated by
[`examples/agents.yml`](../examples/agents.yml) and described by
[`examples/config.schema.json`](../examples/config.schema.json). YAML is
declarative data: validate it before compiling or running anything.

**Read first:** `AGENTS.md`, `.ai/skills/yaml-schema.md`, `docs/YAML_SPEC.md`,
`docs/adr/0002-yaml-is-compiled-not-executed-directly.md`.

## Scope

- Define typed schema models for `system`, `defaults`, `mcps`, `tools`,
  `resolvers`, `orchestrators`, `agents`, and `graph`.
- Implement a safe loader (file/string → schema models).
- Implement semantic validation beyond JSON Schema.

## Files allowed to change

- `src/agentplatform/spec/**`
- `src/agentplatform/validation/**`
- `tests/spec/**`, `tests/validation/**`
- Test fixtures under `tests/fixtures/**`

## Requirements

- Schema models are typed and reject unknown keys.
- Loader parses YAML safely.
- Validation collects all errors with clear locations and enforces at least:
  - required top-level `system` and `graph`;
  - exactly one root in `graph`;
  - every graph node id exists in `orchestrators` or `agents`;
  - graph has no cycles;
  - resolver ids referenced by nodes exist in top-level `resolvers`;
  - tool ids referenced by agents exist in top-level `tools`;
  - MCP ids referenced by agents exist in top-level `mcps`;
  - orchestrators declare `prompts.orchestrator`;
  - prompt fields use the `prompts:` object shape;
  - node model overrides are full replacements with required fields;
  - `protected: true` requires a fixed access plugin at startup or produces a
    configuration error;
  - no literal secrets in YAML.
- Provide a single public entry point, e.g. `validate_spec(data) -> Result`.

## Out of scope

- Compilation into the agent graph (task 0003).
- Runtime, prompt rendering, plugin invocation, MCP connections, API.
- Inventing YAML features not in `docs/YAML_SPEC.md`.

## Acceptance criteria

- [ ] `examples/agents.yml` loads and validates with no errors.
- [ ] Invalid samples produce structured, located error messages.
- [ ] Dangling graph/resolver/tool/MCP references are caught.
- [ ] Missing/multiple roots and cycles are caught.
- [ ] Unknown keys and literal secrets are rejected.
- [ ] Loader uses safe parsing; no code execution from YAML.
- [ ] Tests cover valid and invalid cases.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm validation runs before compilation, YAML is
treated as data, and no secrets are accepted. Recommend task 0003 next.
