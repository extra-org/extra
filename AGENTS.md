# AGENTS.md

This file is the operating manual for **every AI coding agent** working in this
repository. Read it fully before making any change. If a task ever conflicts
with this file, this file wins — stop and ask for clarification.

---

## 1. Project mission

This repository is a **declarative platform for building AI agent systems**.

Developers describe an agent system in YAML. The platform validates that YAML,
compiles it into a typed internal graph, and runs it through a long-lived
runtime that renders prompt files, calls resolver/tool plugins and MCP servers,
exposes an API, and produces execution traces.

The product is **not built yet**. The repository is currently in the
**foundation phase**: documentation, architecture decisions, agent skills, and
task definitions. Future agents implement the product task-by-task using the
files in `tasks/`.

---

## 2. Core architecture pipeline

```
config.yml
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
  → RuntimeEngine
  → filter protected nodes through access plugin
  → route through graph
  → resolve prompt variables through resolver plugins
  → render prompts
  → execute selected orchestrator/agent
  → call configured tools/MCP as needed
  → return response + trace
```

**Three separated phases (do not collapse them):** (1) **build/compile** —
load, validate, and compile YAML into a `CompiledAgentGraph` *before*
serving requests (never executes requests); (2) **runtime/execution** — per
request, create an `ExecutionContext`, filter protected nodes, route to a node
instance, render prompts, execute, call tools/MCP, return response + trace;
(3) **client extension** — client-specific auth/business logic lives in
plugins, never in the generic runtime.
→ See [ADR 0007](docs/adr/0007-build-phase-separate-from-runtime-phase.md).

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
    through plugins**, not baked into the generated runtime.
12. **Nodes declare what they need** (resolvers, tools, MCPs). **The runtime
    resolves and binds** those needs.
13. **Protected nodes must fail closed** through the fixed access plugin.
14. **Prompt text alone is not a security boundary.** Enforcement happens at the
    tool/data layer.
15. **Secrets must never be stored in YAML or prompt files.**
16. **Every meaningful behavior must have tests.**

See `docs/adr/` for the rationale behind the most important rules. In
particular, **read
[ADR 0005](docs/adr/0005-prompt-rendering-and-context-resolution.md) before
changing prompt rendering or resolver behavior**,
and **read
[ADR 0006](docs/adr/0006-reusable-agent-definitions-and-hierarchy-instances.md)
before changing how reusable node declarations, graph instances, or execution
work** (the runtime executes compiled *instances*, not raw declarations), and
**read
[ADR 0007](docs/adr/0007-build-phase-separate-from-runtime-phase.md) before
blurring the build, runtime, and client-extension phases.**

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
├── .ai/                       ← canonical agent instructions (skills/roles/workflows)
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
├── context/       ← resolver plugins + access plugin integration
├── tools/         ← tool plugin + MCP integration
├── observability/ ← tracing
├── api/           ← HTTP API
└── cli/           ← command-line interface
```

---

## 5. AI Instruction System

The **canonical instruction system lives under [`.ai/`](.ai/)** — it is the
single source of truth for how AI agents (Claude Code, Codex, Cursor, or any
future tool) work in this repo. **Before doing any work, read this file
(`AGENTS.md`) and [`.ai/README.md`](.ai/README.md).**

- [`.ai/skills/`](.ai/skills/) — reusable operational playbooks (how to do a
  kind of work well).
- [`.ai/roles/`](.ai/roles/) — reusable role definitions (architect,
  code-reviewer, test-engineer, documentation-writer).
- [`.ai/workflows/`](.ai/workflows/) — task workflows (feature-task, code-review,
  testing, documentation-update).

**Tool-specific folders (`.claude/`, `.codex/`, `.agents/`, `.cursor/`) must not
duplicate instruction content.** They hold only tool configuration and thin
adapter READMEs that point back to `.ai/`. If a future tool needs a specific
format, generate **thin adapters only** — never copy skills, roles, or workflows.

Always read [`.ai/skills/project-architecture.md`](.ai/skills/project-architecture.md)
first; then pick by the *kind* of task and the *area* of the system.

> **Rule: if a task touches multiple areas, read all relevant skills before
> editing.** (e.g. implementing the runtime in Python while adding tests →
> read the senior-python-engineering, runtime-engine, and testing skills.)

### Task → skill mapping (practice skills)

| Task type                         | Read this skill                                 |
| --------------------------------- | ----------------------------------------------- |
| Python implementation task        | `.ai/skills/senior-python-engineering.md`       |
| Test task                         | `.ai/skills/testing.md`                          |
| Code review task                  | `.ai/skills/code-review.md`                      |
| Architecture change               | `.ai/skills/architecture-review.md`              |
| Refactor                          | `.ai/skills/refactoring.md`                      |
| Documentation update              | `.ai/skills/documentation.md`                    |
| Git / change management           | `.ai/skills/git-workflow.md`                     |
| New skill creation                | `.ai/skills/skill-authoring.md`                  |
| Anything (always, first)          | `.ai/skills/project-architecture.md`             |

### Area → skill mapping (system-specific skills)

| If you are working on…                     | Read this skill                       |
| ------------------------------------------ | ------------------------------------- |
| YAML schema, loading, validation           | `.ai/skills/yaml-schema.md`           |
| RuntimeEngine, ExecutionContext, lifecycle | `.ai/skills/runtime-engine.md`        |
| Prompt templates / rendering               | `.ai/skills/prompt-rendering.md`      |
| Plugin context/access resolution           | `.ai/skills/sidecar-auth-context.md`  |
| Tools, MCP servers, permissions            | `.ai/skills/mcp-tools.md`             |

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
`0004` runtime engine → `0005` prompts → `0006` plugin context/access → `0007` tools/MCP →
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
- **No client-specific business logic** in the runtime — that belongs in
  customer plugins.
- **Tests accompany behavior.** New meaningful behavior ships with tests.

---

## 8. Testing and validation commands

Run these from the repository root:

```bash
make install   # editable install (-e ".[dev]") with dev dependencies
make format     # auto-format the codebase (ruff format)
make lint       # lint (ruff check)
make typecheck  # type-check (mypy)
make test       # run the test suite (pytest)
make check      # lint + typecheck + test (the gate that must pass)
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
- **Never** change public contracts (YAML schema, plugin contracts, API shape)
  without an ADR and explicit approval.
- If a change would touch many files or alter architecture, **stop and propose
  it as its own task** instead of doing it inline.
- When in doubt, do less and ask.

---

## Claude Code Integration

This repository is prepared for **Claude Code**. See
`docs/CLAUDE_CODE_WORKFLOW.md` for the full workflow.

- **`CLAUDE.md` is the project entrypoint** Claude Code reads first. It mirrors
  this manual's rules and points to `.ai/`; if the two ever disagree,
  **`AGENTS.md` wins**.
- **Skills are not duplicated under `.claude/`.** `.claude/skills/` contains
  **thin pointer files only** — one per `.ai/skills/<name>.md`, each containing
  a single line that reads and applies the source skill. No logic lives there.
  `.ai/` remains the single source of truth (see §5). `.claude/` otherwise holds
  only `settings.json` (tool permissions) and a thin `README.md` that points to `.ai/`.
- **Role/persona definitions** (`architect`, `code-reviewer`, `test-engineer`,
  `documentation-writer`) live under [`.ai/roles/`](.ai/roles/), not in
  `.claude/agents/`.
- **Shared, conservative settings live in `.claude/settings.json`.** Local/private
  config (`CLAUDE.local.md`, `.claude/settings.local.json`) is git-ignored.

### Per-task rule

For each task, **select the relevant skill(s) before editing**. If a task spans
multiple areas, read all relevant skills first. In particular:

- Task touches **architecture** → use `.ai/skills/architecture-review.md`.
- Task touches **tests** → use `.ai/skills/testing.md`.
- Task touches **Python code** → use `.ai/skills/senior-python-engineering.md`.

The full task→skill and area→skill mappings are in §5 (AI Instruction System)
and in `CLAUDE.md`.
