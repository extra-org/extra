# `.claude/` — Claude Code adapter (no duplicated instructions)

Claude-specific duplicate skills and agents are intentionally **not** stored
here. The canonical, tool-agnostic project instructions live under [`.ai/`](../.ai/).

Start with:

- [`../AGENTS.md`](../AGENTS.md) — repo-wide rules for all agents.
- [`../.ai/README.md`](../.ai/README.md) — the instruction-system index.
- [`../.ai/skills/`](../.ai/skills/) — reusable operational playbooks.
- [`../.ai/roles/`](../.ai/roles/) — reusable agent personas.
- [`../.ai/workflows/`](../.ai/workflows/) — task workflows.

This folder contains only Claude-specific **tool configuration**:

- `settings.json` — shared, conservative permissions for Claude Code.

Local/private config (`CLAUDE.local.md`, `.claude/settings.local.json`) is
git-ignored.

Generated Claude skills live under `.claude/skills/<name>/SKILL.md`.
Regenerate them with `make sync-skills` after editing `.ai/skills/`. These files
contain the full skill content from `.ai/skills/<name>.md` — do not edit them
directly.
