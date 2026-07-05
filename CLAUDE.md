# CLAUDE.md

Declarative platform for building AI agent systems. See `docs/` for architecture details and `tasks/` for current work.

w## Naming

The product/brand name is **Extra** (matches the GitHub repo, docs site, and `docs.json`). `agent-engine` is only the PyPI/package name of the core engine; `agentctl` and `agent-manager` are the CLI/console scripts. Don't call the product "Agent Engine" in user-facing copy (README, docs landing page) — use "Extra".

## Skills

Skills, roles, and workflows live in `.ai/` — the single source of truth. `.claude/skills/` is generated from there (`make generate-ai`). Do not edit generated files directly.

## Commands

```bash
make generate-ai   # regenerate .claude/, .codex/ from .ai/
make format        # ruff format
make lint          # ruff check
make typecheck     # mypy
make test          # pytest
make check         # lint + typecheck + test — must pass before finishing a task
```

## Guardrails

- Do not skip tests or hardcode secrets.
- Do not change architecture decisions without explicit approval.
- Do not edit `.claude/skills/`, `.claude/agents/`, or `.claude/workflows/` directly.
