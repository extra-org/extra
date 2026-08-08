"""Tests for `agentctl generate`: artifacts are created, and a second run skips."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from agentctl.main import cli
from tests.fixtures.utils import fixture_path

FIXTURE_YAML = fixture_path("test_system", "agents.yaml")


def test_generate_reports_unused_declared_tools(tmp_path: Path) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
system:
  name: test
defaults:
  model: {provider: anthropic, name: claude-test}
tools:
  used_tool:
    description: Used tool
  unused_tool:
    description: Unused tool
agents:
  worker:
    description: Worker
    tools: [used_tool]
graph:
  worker:
""".lstrip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["generate", "--config", str(config)])

    assert result.exit_code == 0
    assert "  ignore  unused_tool  (declared but not referenced by any agent)" in result.output
    assert (tmp_path / "plugins" / "tools" / "used_tool.py").is_file()
    assert not (tmp_path / "plugins" / "tools" / "unused_tool.py").exists()

    second = CliRunner().invoke(cli, ["generate", "--config", str(config)])

    assert second.exit_code == 0
    assert "  ignore  unused_tool  (declared but not referenced by any agent)" in second.output
    assert "Nothing to generate — no referenced stubs are missing." in second.output


def test_generate_creates_all_declared_artifacts(tmp_path: Path) -> None:
    dest = tmp_path / "agents.yaml"
    dest.write_text(FIXTURE_YAML.read_text(), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["generate", "--config", str(dest)])
    assert result.exit_code == 0, result.output

    assert (tmp_path / "plugins" / "tools" / "echo_tool.py").is_file()
    assert (tmp_path / "plugins" / "resolvers" / "shared.py").is_file()
    assert (tmp_path / "plugins" / "resolvers" / "greeting_agent.py").is_file()
    assert (tmp_path / "plugins" / "plugins.toml").is_file()


def test_generate_is_idempotent(tmp_path: Path) -> None:
    dest = tmp_path / "agents.yaml"
    dest.write_text(FIXTURE_YAML.read_text(), encoding="utf-8")
    runner = CliRunner()

    first = runner.invoke(cli, ["generate", "--config", str(dest)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli, ["generate", "--config", str(dest)])
    assert second.exit_code == 0, second.output
    assert "Nothing to generate" in second.output
