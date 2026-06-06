# CLAUDE.md

Project entrypoint for **Claude Code**. Read this first, then follow the links
below. This file is a concise operating guide; `AGENTS.md` is the full manual and
takes precedence if anything conflicts.

This repository uses a **generic, tool-agnostic AI instruction system**. The
canonical instruction source is [`.ai/`](.ai/) — shared by Claude Code, Codex,
Cursor, and any future tool. Claude Code should start from `AGENTS.md`,
`CLAUDE.md`, and [`.ai/README.md`](.ai/README.md), and read the relevant
`.ai/skills/*` file **before editing**.

`.claude/skills/` is **generated output** — do not edit it. Run
`make sync-skills` (or `make install`) to regenerate it from `.ai/skills/`.
`.ai/` is the single source of truth for all skills.

---

## 1. Project mission

A **declarative platform for building AI agent systems**. Developers describe an
agent system in YAML; the platform validates it, compiles it into a typed agent
graph, and runs it through a long-lived runtime that renders prompts per request,
integrates MCP/tools, resolves client-specific context via a sidecar, exposes an
API, and traces execution.

**Status: foundation phase.** The runtime, compiler, YAML parser, MCP, sidecar,
and API are **not implemented yet**. Work proceeds task-by-task via `tasks/`.

## 2. Core architecture pipeline

```
agent.yml → validate → compile → CompiledAgentGraph → RuntimeEngine
          → ExecutionContext (per request) → recursive execution → response + trace
```

## 3. Non-negotiable architecture rules

1. YAML is declarative specification, **not** executable business logic.
2. The runtime must **never** execute raw YAML dictionaries directly.
3. YAML is **validated before compilation**.
4. Validated YAML is **compiled into typed internal models**.
5. **`RuntimeEngine` is created once** at application startup.
6. **`ExecutionContext` is created per request.**
7. No request state on `RuntimeEngine` or the compiled graph.
8. Prompt files are **templates**; raw templates may be cached.
9. **Rendered prompts are created per request** (never globally cached).
10. Client-specific auth/authorization/business context lives in the
    **sidecar/plugin** boundary, not the runtime.
11. **Prompt text is not a security boundary.**
12. Tool permissions and injected parameters are **enforced outside prompts**.
13. **Secrets must never be committed** (not in YAML, prompts, or config).

Full rationale lives in `docs/adr/`.

## 4. What Claude should read first

Before implementing feature work, read:

- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_LIFECYCLE.md`
- `docs/SIDECAR_CONTEXT_AUTH.md`
- `docs/PROMPT_RENDERING.md`
- `.ai/README.md`

For any task, also open the relevant file in `tasks/` and work within its scope.

## 5. How to use skills

All skills, roles, and workflows live under **`.ai/`** — the single,
tool-agnostic source of truth. There is **no** Claude-specific copy; `.claude/`
holds only `settings.json` and a README that points here.

Always read `.ai/skills/project-architecture.md` first. Then pick the skill(s)
for your task. **If a task touches multiple areas, read all relevant skills
before editing.**

For specific work, use the relevant skill:

- Code review: `.ai/skills/code-review.md`
- Testing: `.ai/skills/testing.md`
- Python implementation: `.ai/skills/senior-python-engineering.md`
- Architecture: `.ai/skills/architecture-review.md`
- Refactoring: `.ai/skills/refactoring.md`
- Documentation: `.ai/skills/documentation.md`
- YAML schema: `.ai/skills/yaml-schema.md`
- Runtime engine: `.ai/skills/runtime-engine.md`
- Prompt rendering: `.ai/skills/prompt-rendering.md`
- Sidecar auth/context: `.ai/skills/sidecar-auth-context.md`
- MCP/tools: `.ai/skills/mcp-tools.md`

Workflows that combine skills end-to-end live in `.ai/workflows/`.

## 6. How to choose roles

Reusable agent personas live in **`.ai/roles/`**:

- **architect** (`.ai/roles/architect.md`) — architecture planning/review;
  read-only, does not implement unless explicitly asked.
- **code-reviewer** (`.ai/roles/code-reviewer.md`) — senior structured review.
- **test-engineer** (`.ai/roles/test-engineer.md`) — plans and writes pytest
  tests; never calls real external services.
- **documentation-writer** (`.ai/roles/documentation-writer.md`) — updates
  README/docs/ADRs/skills honestly.

Adopt a role when the work matches its purpose; otherwise work directly using
the relevant skill.

## 7. Required validation commands

```bash
make format   # auto-format (ruff)
make lint     # ruff + mypy
make test     # pytest
make check    # format-check + lint + test  ← must pass before finishing a task
```

Foundation-phase note: until task `0001` installs tooling, these targets print a
notice and exit cleanly. That is expected.

## 8. Final response format

After a task, report:

1. **Summary** — what changed and why.
2. **Files changed** — created/modified/deleted.
3. **Architecture rules respected** — confirm the relevant rules from §3.
4. **Commands run + results** — e.g. `make check`.
5. **Acceptance criteria** — checked against the task file.
6. **Out of scope / not done.**
7. **Recommended next task.**
8. **Risks / notes.**

## Guardrails

- Do not implement multiple tasks at once or rewrite architecture casually.
- Do not skip tests; do not hardcode secrets.
- Do not change architecture decisions without an ADR (see
  `.ai/skills/architecture-review.md`).
- Do not recreate `.claude/skills/` or `.claude/agents/`; do not duplicate
  `.ai/` content. `.claude/` holds only `settings.json` and a thin `README.md`.
