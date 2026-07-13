"""YAML parsing of the optional agent-level ``auto`` flag (alias ``auto_mode``)."""

from __future__ import annotations

from pathlib import Path

from agent_engine.core.spec import AgentSpec
from agent_engine.engine.langgraph.helpers import walk
from agent_engine.parsers.yaml.parser import YAMLParser

_BASE = """
system:
  name: t
defaults:
  model: {{provider: anthropic, name: claude-x}}
tools:
  send_email: {{description: send email}}
agents:
  writer:
    description: writes things
    tools: [send_email]
{auto}
graph:
  writer:
"""


def _parse(tmp_path: Path, auto: str) -> AgentSpec:
    text = _BASE.format(auto=auto)
    path = tmp_path / "agents.yaml"
    path.write_text(text, encoding="utf-8")
    spec = YAMLParser().parse(str(path))
    agent = next(n.node for n in walk(spec.graph) if isinstance(n.node, AgentSpec))
    return agent


def test_auto_true_parsed(tmp_path: Path) -> None:
    assert _parse(tmp_path, "    auto: true").auto_mode is True


def test_auto_false_parsed(tmp_path: Path) -> None:
    assert _parse(tmp_path, "    auto: false").auto_mode is False


def test_auto_mode_alias_still_parsed(tmp_path: Path) -> None:
    assert _parse(tmp_path, "    auto_mode: true").auto_mode is True


def test_auto_missing_defaults_false(tmp_path: Path) -> None:
    assert _parse(tmp_path, "").auto_mode is False
