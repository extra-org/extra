# `.ai/` — Canonical AI-Agent Instruction System

This directory is the **single source of truth** for how AI coding agents work
in this repository. It is tool-agnostic: Claude Code, Codex, Cursor, and any
future coding agent read the same instructions from here.

> Before doing any work, read [`../AGENTS.md`](../AGENTS.md) and this file.

## What lives here

| Directory     | Contains                                                              |
| ------------- | --------------------------------------------------------------------- |
| `skills/`     | Reusable **operational playbooks** — how to do a kind of work well.   |
| `roles/`      | Reusable **agent personas** — focused roles with a mission and scope. |
| `workflows/`  | **Task workflows** — how to execute common end-to-end tasks.          |

### `skills/` — operational playbooks

Each skill is a short, repeatable playbook for one discipline or area: its
purpose, when to use it, what to read first, core principles, a process,
checklists, and common mistakes. Skills explain **how** to work; the
[`../tasks/`](../tasks/) files define **what** to build, in order.

How to use skills:

1. **Always start** with [`skills/project-architecture.md`](skills/project-architecture.md)
   — it applies to every change.
2. Read the **practice skill** for the *kind* of work (review, testing,
   engineering, refactoring, docs, git, architecture review).
3. Read the **area skill** for the *part of the system* you touch (YAML,
   runtime, prompts, sidecar, tools).
4. If a task spans multiple areas, read all relevant skills before editing.
5. Follow the process, finish the checklist, and produce the skill's report.

Practice / discipline skills:

| Skill                                                                      | Use when…                                          |
| -------------------------------------------------------------------------- | -------------------------------------------------- |
| [project-architecture](skills/project-architecture.md)                     | Anything — overall rules and layout (always).      |
| [senior-python-engineering](skills/senior-python-engineering.md)           | Writing/structuring Python code.                   |
| [code-review](skills/code-review.md)                                       | Reviewing a change (others' or your own).          |
| [architecture-review](skills/architecture-review.md)                       | A change affects layers, lifecycles, or contracts. |
| [refactoring](skills/refactoring.md)                                       | Restructuring code without changing behavior.      |
| [testing](skills/testing.md)                                               | Writing or running tests.                          |
| [documentation](skills/documentation.md)                                   | Editing docs, README, AGENTS.md, or ADRs.          |
| [git-workflow](skills/git-workflow.md)                                     | Branching, staging, committing.                    |
| [skill-authoring](skills/skill-authoring.md)                               | Creating or restructuring a skill.                 |

Area skills:

| Skill                                                        | Use when working on…                        |
| ------------------------------------------------------------ | ------------------------------------------- |
| [yaml-schema](skills/yaml-schema.md)                         | YAML schema, loading, validation.           |
| [runtime-engine](skills/runtime-engine.md)                   | RuntimeEngine, ExecutionContext, lifecycle. |
| [prompt-rendering](skills/prompt-rendering.md)               | Prompt templates and rendering.             |
| [sidecar-auth-context](skills/sidecar-auth-context.md)       | Plugin context and protected-node access.   |
| [mcp-tools](skills/mcp-tools.md)                             | Tool plugins and MCP servers.               |

### `roles/` — agent personas

Specialized personas an agent can adopt. Each role states a mission, what to
read first, what it enforces or produces, and its expected output.

| Role                                                       | Purpose                                               |
| ---------------------------------------------------------- | ----------------------------------------------------- |
| [architect](roles/architect.md)                            | Plan and review architecture; decide if an ADR is needed. |
| [code-reviewer](roles/code-reviewer.md)                    | Senior-level review of diffs/PRs; structured report.  |
| [test-engineer](roles/test-engineer.md)                    | Plan and write behavior-focused pytest tests.         |
| [documentation-writer](roles/documentation-writer.md)      | Keep docs/ADRs honest and synchronized with code.     |

### `workflows/` — task workflows

End-to-end recipes that combine roles and skills for a common task.

| Workflow                                                       | Use for…                                       |
| -------------------------------------------------------------- | ---------------------------------------------- |
| [feature-task](workflows/feature-task.md)                      | Implementing a numbered task in `tasks/`.      |
| [code-review](workflows/code-review.md)                        | Reviewing a change before merge.               |
| [testing](workflows/testing.md)                                | Adding or running tests for a change.          |
| [documentation-update](workflows/documentation-update.md)      | Updating docs/ADRs after a change.             |

## Tool-specific folders must not duplicate instructions

`.claude/`, `.codex/`, `.cursor/`, etc. exist only for **tool configuration**
(permissions, model settings) and **thin adapters** that point back here. They
must **not** contain copied skills, roles, or workflows.

If a future tool needs a specific format (e.g. per-skill `SKILL.md` directories
or `.toml` agent definitions), generate **thin adapters that reference `.ai/`**
— never copy the content. Keeping one source of truth is the whole point of
this directory.
