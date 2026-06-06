#!/usr/bin/env python3
"""Generate tool-specific skill pointer files from .ai/skills/.

Run: python scripts/sync_skills.py
Generates thin adapters for Claude Code, Cursor, and Codex.
.ai/skills/ is the single source of truth — edit there, never in the adapters.

Adapter locations:
  .claude/skills/<name>.md   — Claude Code slash commands
  .cursor/rules/<name>.mdc   — Cursor agent-requested rules
  .codex/skills/<name>.md    — Codex skill instructions
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / ".ai" / "skills"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
FIELD_RE = re.compile(r"^(name|description):\s*(.+)$", re.MULTILINE)

GENERATED_MARKER = "generated: true"

CLAUDE_TEMPLATE = """\
---
name: {name}
description: {description}
generated: true
---

Read and apply `.ai/skills/{name}.md`.
"""

CURSOR_TEMPLATE = """\
---
description: {description}
alwaysApply: false
generated: true
---

Read and apply `.ai/skills/{name}.md`.
"""

CODEX_TEMPLATE = """\
---
name: {name}
description: {description}
generated: true
---

Read and apply `.ai/skills/{name}.md`.
"""

ADAPTERS: list[tuple[Path, str, str]] = [
    (ROOT / ".claude" / "skills", "{name}.md", CLAUDE_TEMPLATE),
    (ROOT / ".cursor" / "rules", "{name}.mdc", CURSOR_TEMPLATE),
    (ROOT / ".codex" / "skills", "{name}.md", CODEX_TEMPLATE),
]


def extract(path: Path) -> dict[str, str]:
    m = FRONTMATTER_RE.match(path.read_text())
    return dict(FIELD_RE.findall(m.group(1))) if m else {}


def is_generated(path: Path) -> bool:
    try:
        return path.read_text().startswith(GENERATED_MARKER)
    except OSError:
        return False


def sync() -> int:
    skills = sorted(SOURCE.glob("*.md"))
    source_names: set[str] = set()

    for skill in skills:
        fields = extract(skill)
        name = fields.get("name") or skill.stem
        description = fields.get("description", "")
        source_names.add(name)

        for target_dir, filename_tpl, template in ADAPTERS:
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = filename_tpl.format(name=name)
            (target_dir / filename).write_text(
                template.format(name=name, description=description)
            )

    for target_dir, _, _ in ADAPTERS:
        for candidate in target_dir.glob("*"):
            if candidate.stem not in source_names and is_generated(candidate):
                candidate.unlink()
                print(f"  removed stale {candidate.relative_to(ROOT)}")

    print(f"synced {len(source_names)} skill(s) →")
    for target_dir, _, _ in ADAPTERS:
        print(f"  {target_dir.relative_to(ROOT)}/")

    return 0


if __name__ == "__main__":
    sys.exit(sync())
