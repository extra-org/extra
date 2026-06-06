# Task 0001 — Repository Foundation (package skeleton)

## Goal

Create the Python package skeleton and test layout that all later tasks build
on, matching the planned layout in `AGENTS.md`. No product behavior yet.

## Context

The repository currently has docs, the `.ai/` instruction system, tasks, a
`Makefile`, and `pyproject.toml`, but no `src/` package. Later tasks (0002+) assume a package at
`src/agentplatform/` with per-layer subpackages. This task creates that empty,
importable skeleton so tooling (`ruff`, `mypy`, `pytest`) has something to act
on.

**Read first:** `AGENTS.md`, `.ai/skills/project-architecture.md`,
`.ai/skills/testing.md`.

## Scope

- Create the `src/agentplatform/` package with empty per-layer subpackages.
- Create a matching `tests/` tree with a trivial passing test.
- Ensure `make install`, `make lint`, `make test`, `make check` run against the
  skeleton without errors.

## Files allowed to change

- `src/agentplatform/**` (new)
- `tests/**` (new)
- `pyproject.toml` (only if needed to make the package importable/installable)
- `README.md`, `docs/ROADMAP.md` (only to flip the foundation status if needed)

## Requirements

- Create `src/agentplatform/__init__.py` exposing a `__version__`.
- Create empty subpackages with `__init__.py` (and a short module docstring
  stating the layer's responsibility): `spec`, `validation`, `compiler`,
  `graph`, `runtime`, `prompts`, `context`, `tools`, `observability`, `api`,
  `cli`.
- Do **not** implement any layer logic — docstrings/placeholders only.
- Create `tests/test_smoke.py` that imports the package and asserts
  `__version__` is a string.
- Ensure `pip install -e ".[dev]"` works and `pytest` collects/passes.

## Out of scope

- YAML loading, validation, compilation, runtime, prompts, plugins, tools, CLI,
  API (later tasks).
- Any business logic or feature behavior.

## Acceptance criteria

- [ ] `src/agentplatform/` and all listed subpackages exist and are importable.
- [ ] Each subpackage `__init__.py` has a one-line docstring naming its layer.
- [ ] `tests/test_smoke.py` passes.
- [ ] `make install` succeeds.
- [ ] `make check` passes (lint clean, tests green).
- [ ] No layer logic implemented; no secrets added.

## Commands to run before finishing

```bash
make install
make check
```

## Expected final report

Use the AGENTS.md §9 format: summary, files changed, architecture rules
respected, `make check` result, acceptance-criteria checklist, out-of-scope
notes, recommended next task (0002), and any risks.
