# Development Workflow

How to work in this repository, for humans and AI agents alike.

---

## Before you start

1. Read [AGENTS.md](../AGENTS.md) (the operating manual).
2. Read the relevant guide in [`.ai/skills/`](../.ai/skills/) (start with
   [`.ai/README.md`](../.ai/README.md)).
3. Open the task in [`tasks/`](../tasks/) and read its **Goal, Scope, Files
   allowed to change, and Out of scope**.

## The loop

```
pick the next task → read its skill(s) → make a small change → run `make check`
  → repeat until acceptance criteria are met → report using the AGENTS.md format
```

## Environment

- Python 3.11+.
- Tooling: `ruff` (format + lint), `mypy` (types), `pytest` (tests), configured
  in `pyproject.toml`.
- Set up with `make install`.

## Commands

| Command        | Purpose                                                      |
| -------------- | ------------------------------------------------------------ |
| `make install` | Install dependencies / set up the dev environment.           |
| `make sync-ai` | Regenerate tool adapters from `.ai/`.                        |
| `make format`  | Auto-format code (ruff).                                     |
| `make lint`    | Static analysis (ruff check).                                |
| `make typecheck` | Type-check the codebase (mypy).                            |
| `make test`    | Run the test suite (pytest).                                 |
| `make check`   | lint + typecheck + test. **Must pass before finishing.**     |
| `make validate` | Offline: validate the flagship example (no LLM/network calls). |
| `make inspect` | Offline: summarize the flagship example (agents/MCPs/hooks).  |
| `make clean`   | Remove caches and build artifacts.                           |

Current CLI checks:

```bash
agentctl version
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml
```

## Coding standards

- Follow the **planned package layout** in `AGENTS.md`. No giant single-file
  modules.
- Keep modules single-responsibility, matching the architecture layers.
- Type everything; `mypy` runs in strict mode.
- **Tests accompany behavior** — new meaningful behavior ships with tests.
- No comments that merely narrate code; comment intent/constraints only.

## Git

- Small, focused commits with clear messages.
- One task per branch where practical.
- Do not commit secrets, `.env` files, or large binaries.
- → See [`.ai/skills/git-workflow.md`](../.ai/skills/git-workflow.md).

## Staying in scope

- Only touch files listed in the task's "Files allowed to change".
- Discovered out-of-scope work → propose a **new task**, don't expand the
  current one.
- Contract changes (YAML schema, plugin contracts, API shape) require a
  **design note** and explicit approval.

## Definition of done (per task)

- [ ] Acceptance criteria in the task file are all met.
- [ ] `make check` passes.
- [ ] Docs updated if behavior/contract changed.
- [ ] Reported using the final response format in AGENTS.md §9.
