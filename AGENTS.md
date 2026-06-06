# AGENTS.md

This file is the operating manual for **every AI coding agent** working in this
repository. Read it fully before making any change. If a task ever conflicts
with this file, this file wins — stop and ask for clarification.

---

## 1. Project mission

This repository is a **declarative platform for building AI agent systems**.

Developers describe an agent system in YAML. The platform validates that YAML,
compiles it into a typed internal agent graph, and runs it through a long-lived
runtime that renders prompts, calls tools/MCP servers, resolves client-specific
context through a sidecar, exposes an API, and produces execution traces.

The product is **not built yet**. The repository is currently in the
**foundation phase**: documentation, architecture decisions, agent skills, and
task definitions. Future agents implement the product task-by-task using the
files in `tasks/`.

---

## 2. Core architecture pipeline

```
agent.yml
  → validate          (schema + semantic validation)
  → compile           (typed internal models)
  → CompiledAgentGraph (immutable, built once)
  → RuntimeEngine     (long-lived, created at startup)
  → ExecutionContext  (created per request)
  → recursive agent execution
  → response + trace
```

Per-request flow:

```
Incoming request
  → Security/Context Gate
  → optional pre-routing sidecar call
  → RuntimeEngine
  → route through hierarchy
  → optional pre-agent sidecar call
  → resolve context
  → render prompts
  → execute selected agent
  → enforce tool permissions
  → return response + trace
```

---

## 3. Non-negotiable architecture rules

These rules are binding. Do not violate them, even if a task description seems
to ask for it. If a rule blocks you, stop and raise it.

1. **YAML is declarative specification, not executable business logic.**
2. **The runtime must never execute raw YAML dictionaries directly.**
3. **YAML must be validated first.**
4. **Validated YAML must be compiled into internal typed models** before use.
5. **RuntimeEngine is created once at application startup.** Never per request.
6. **ExecutionContext is created per request.** Never reused across requests.
7. **Do not store request state on RuntimeEngine** or on the compiled graph.
8. **Prompt files are templates.** They contain placeholders, not final text.
9. **Prompt templates may be cached.** Compiled/parsed templates are reusable.
10. **Prompt values are resolved dynamically per request.** Never cache a
    fully-rendered prompt globally.
11. **Client-specific auth, authorization, and business context are handled
    through a sidecar/plugin model**, not baked into the generated runtime.
12. **Agents declare what they need** (context, permissions, tools). **The
    runtime resolves and enforces** those needs.
13. **Tools must enforce permissions and injected parameters.**
14. **Prompt text alone is not a security boundary.** Enforcement happens at the
    tool/data layer.
15. **Secrets must never be stored in YAML or prompt files.**
16. **Every meaningful behavior must have tests.**

See `docs/adr/` for the rationale behind the most important rules. In
particular, **read
[ADR 0005](docs/adr/0005-prompt-rendering-and-context-resolution.md) before
changing prompt rendering, context resolver, sidecar, or tool-policy behavior.**

---

## 4. Repository structure

```
.
├── AGENTS.md                  ← you are here
├── README.md                  ← public-facing overview
├── Makefile                   ← task runner (install/test/lint/format/check)
├── pyproject.toml             ← tooling + dependency configuration
├── .gitignore
├── docs/                      ← architecture and design documentation
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   ├── YAML_SPEC.md
│   ├── RUNTIME_LIFECYCLE.md
│   ├── SIDECAR_CONTEXT_AUTH.md
│   ├── PROMPT_RENDERING.md
│   ├── MCP_AND_TOOLS.md
│   ├── DEVELOPMENT_WORKFLOW.md
│   └── adr/                   ← architecture decision records
├── skills/                    ← operational guides for agents (read before work)
├── tasks/                     ← small, ordered implementation tasks
└── src/agentplatform/         ← (PLANNED) implementation, created by task 0001
```

**Planned package layout** (do not assume it exists yet; task 0001 creates it):

```
src/agentplatform/
├── spec/          ← YAML loading + schema models
├── validation/    ← validators
├── compiler/      ← spec → CompiledAgentGraph
├── graph/         ← CompiledAgentGraph + typed models
├── runtime/       ← RuntimeEngine + ExecutionContext
├── prompts/       ← template loading + rendering
├── context/       ← context resolvers + sidecar client
├── tools/         ← tool + MCP integration + permission enforcement
├── observability/ ← tracing
├── api/           ← HTTP API
└── cli/           ← command-line interface
```

---

## 5. Skills System

`skills/` contains operational playbooks. **Before doing any work, choose and
read the relevant skill(s).** Start with `skills/README.md` and always read
`skills/project-architecture-skill.md` first; then pick by the *kind* of task
and the *area* of the system.

> **Rule: if a task touches multiple areas, read all relevant skills before
> editing.** (e.g. implementing the runtime in Python while adding tests →
> read the senior-python, runtime-engine, and testing skills.)

### Task → skill mapping (practice skills)

| Task type                         | Read this skill                              |
| --------------------------------- | -------------------------------------------- |
| Code review task                  | `skills/code-review-skill.md`                |
| Test task                         | `skills/testing-skill.md`                    |
| Python implementation task        | `skills/senior-python-engineering-skill.md`  |
| New skill creation                | `skills/skill-authoring-skill.md`            |
| Architecture change               | `skills/architecture-review-skill.md`        |
| Refactor                          | `skills/refactoring-skill.md`                |
| Documentation update              | `skills/documentation-skill.md`              |
| Git / change management           | `skills/git-workflow-skill.md`               |
| Anything (always, first)          | `skills/project-architecture-skill.md`       |

### Area → skill mapping (system-specific skills)

| If you are working on…                     | Read this skill                          |
| ------------------------------------------ | ---------------------------------------- |
| YAML schema, loading, validation           | `skills/yaml-schema-skill.md`            |
| RuntimeEngine, ExecutionContext, lifecycle | `skills/runtime-engine-skill.md`         |
| Prompt templates / rendering               | `skills/prompt-rendering-skill.md`       |
| Sidecar contract, context/auth resolution  | `skills/sidecar-auth-context-skill.md`   |
| Tools, MCP servers, permissions            | `skills/mcp-tools-skill.md`              |

Each skill ends with an **Expected Final Report** (or validation checklist) —
apply it before declaring work done.

---

## 6. How to choose the correct task file

`tasks/` contains small, ordered units of work. Tasks are intentionally narrow.

1. Work tasks **in numeric order** unless told otherwise; later tasks depend on
   earlier ones.
2. Open the task file and read **Goal, Scope, Files allowed to change, and Out
   of scope** before writing code.
3. **Do only what the task says.** If you discover work outside the task scope,
   note it and propose a new task — do not silently expand scope.

Current order: `0001` foundation → `0002` YAML schema → `0003` compiled graph →
`0004` runtime engine → `0005` prompts → `0006` sidecar → `0007` tools/MCP →
`0008` CLI → `0009` API → `0010` Docker → `0011` observability → `0012` tests
& quality gates.

---

## 7. How to make changes safely

- **Read first.** Read this file, the relevant skill, the relevant task, and the
  files you intend to change before editing.
- **Stay in scope.** Only edit files listed in the task's "Files allowed to
  change". If you must touch others, explain why.
- **Small, reviewable diffs.** Prefer many small, focused changes.
- **Do not delete or rewrite existing work** unless the task explicitly requires
  it and you can justify it.
- **No giant single-file implementations.** Follow the planned package layout.
- **No invented/fake features.** Do not stub something to look done. If it is
  not implemented, say so.
- **No secrets**, ever, in code, YAML, prompts, or fixtures.
- **No client-specific business logic** in the runtime — that belongs in the
  sidecar.
- **Tests accompany behavior.** New meaningful behavior ships with tests.

---

## 8. Testing and validation commands

Run these from the repository root:

```bash
make install   # set up the dev environment / install dependencies
make format     # auto-format the codebase
make lint       # static analysis (ruff, mypy)
make test       # run the test suite (pytest)
make check      # format-check + lint + test (the gate that must pass)
```

`make check` is the **mandatory gate** before declaring a task complete. If the
tooling is not installed yet for an early task, the task file says so and tells
you what to run instead.

---

## 9. Final response format after each task

When you finish a task, respond using exactly this structure:

1. **Summary** — what changed and why (2–5 sentences).
2. **Files changed** — list of created/modified/deleted files.
3. **Architecture rules respected** — confirm the relevant rules from §3.
4. **Commands run + results** — e.g. `make check` output (pass/fail).
5. **Acceptance criteria** — check each criterion from the task file.
6. **Out of scope / not done** — anything intentionally left.
7. **Recommended next task** — usually the next numbered task.
8. **Risks / notes** — anything a reviewer should know.

---

## 10. Rules against large uncontrolled rewrites

- **Never** perform a sweeping rewrite of a module to "clean it up" as a side
  effect of another task.
- **Never** reformat or move files outside your task's scope.
- **Never** change public contracts (YAML schema, sidecar contract, API shape)
  without an ADR and explicit approval.
- If a change would touch many files or alter architecture, **stop and propose
  it as its own task** instead of doing it inline.
- When in doubt, do less and ask.

---

## Claude Code Integration

This repository is prepared for **Claude Code**. See
`docs/CLAUDE_CODE_WORKFLOW.md` for the full workflow.

- **`CLAUDE.md` is the project entrypoint** Claude Code reads first. It mirrors
  this manual's rules; if the two ever disagree, **`AGENTS.md` wins**.
- **Claude-native skills live under `.claude/skills/<name>/SKILL.md`** — concise,
  operational, with frontmatter. Each references the deeper, tool-agnostic
  playbook in root **`skills/`** (the source of truth for *how*). Don't duplicate
  content between them.
- **Claude subagents live under `.claude/agents/`**: `architect` (planning/
  review, read-only), `code-reviewer` (structured review), `test-engineer`
  (pytest, never calls real services), `documentation-writer` (honest docs).
- **Shared, conservative settings live in `.claude/settings.json`.** Local/private
  config (`CLAUDE.local.md`, `.claude/settings.local.json`) is git-ignored.

### Per-task rule

For each task, **select the relevant skill(s) before editing**. If a task spans
multiple areas, read all relevant skills first. In particular:

- Task touches **architecture** → use `.claude/skills/architecture-review/SKILL.md`.
- Task touches **tests** → use `.claude/skills/testing/SKILL.md`.
- Task touches **Python code** → use `.claude/skills/senior-python-engineering/SKILL.md`.

The full task→skill and area→skill mappings are in §5 (Skills System) and in
`CLAUDE.md`.
