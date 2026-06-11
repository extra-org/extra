from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentplatform.cli.main import app

runner = CliRunner()


def test_validate_command_succeeds_for_example() -> None:
    result = runner.invoke(app, ["validate", "examples/agents.yml"])

    assert result.exit_code == 0
    assert "YAML loaded" in result.output
    assert "JSON schema valid" in result.output
    assert "Semantic validation passed" in result.output
    assert "Configuration is valid:" in result.output
    assert "agents.yml" in result.output


def test_validate_command_exits_nonzero_for_invalid_config(tmp_path: Path) -> None:
    config = tmp_path / "agent.yml"
    config.write_text(yaml.safe_dump({"graph": {"root": None}}), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(config)])

    assert result.exit_code == 1
    assert "Configuration validation failed" in result.output
    assert "system" in result.output
