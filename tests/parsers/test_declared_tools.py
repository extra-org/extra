"""Tests that the parser preserves the full top-level tools: registry.

Regression coverage for the `generate` command silently omitting tools that
are declared but never referenced by any agent.
"""

from __future__ import annotations

from pathlib import Path

from agent_engine.parsers.yaml.parser import YAMLParser

_SPEC = """
system:
  name: test

defaults:
  model:
    provider: openai
    name: gpt-4o-mini

tools:
  add_new_user:
    description: Adding new user to the system
  block_card:
    description: Block a bank card

agents:
  admin_agent:
    description: "Admin agent"
    prompts:
      system: prompts/admin.md
    tools: [block_card]

graph:
  admin_agent:
"""


def _parse(tmp_path: Path):
    cfg = tmp_path / "spec.yml"
    cfg.write_text(_SPEC, encoding="utf-8")
    return YAMLParser().parse(str(cfg))


def test_declared_tools_includes_unreferenced_entries(tmp_path: Path) -> None:
    spec = _parse(tmp_path)
    declared_ids = {t.id for t in spec.declared_tools}
    assert declared_ids == {"add_new_user", "block_card"}


def test_declared_tools_carries_descriptions(tmp_path: Path) -> None:
    spec = _parse(tmp_path)
    by_id = {t.id: t.description for t in spec.declared_tools}
    assert by_id["add_new_user"] == "Adding new user to the system"
