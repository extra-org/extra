"""CLI ``run`` logging flags route diagnostics to stderr without polluting stdout."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentplatform.cli.main import app

runner = CliRunner()


def _invalid_config(tmp_path: Path) -> Path:
    config = tmp_path / "agent.yml"
    config.write_text(yaml.safe_dump({"graph": {"root": None}}), encoding="utf-8")
    return config


def test_run_default_suppresses_info_logs(tmp_path: Path) -> None:
    config = _invalid_config(tmp_path)

    result = runner.invoke(app, ["run", str(config), "hi"])

    assert result.exit_code == 1
    # INFO-level diagnostics must not appear without a verbosity flag.
    assert "Loading agent spec from config" not in result.stderr
    # The user-facing failure message still shows.
    assert "Configuration invalid" in result.stderr


def test_run_verbose_emits_info_logs_on_stderr(tmp_path: Path) -> None:
    config = _invalid_config(tmp_path)

    result = runner.invoke(app, ["run", str(config), "hi", "--verbose"])

    assert result.exit_code == 1
    assert "INFO agentplatform.cli.main - Loading agent spec from config" in result.stderr
    # Diagnostics stay off stdout.
    assert "Loading agent spec from config" not in result.stdout


def test_run_debug_emits_debug_logs(tmp_path: Path) -> None:
    config = _invalid_config(tmp_path)

    result = runner.invoke(app, ["run", str(config), "hi", "--debug"])

    assert result.exit_code == 1
    assert "DEBUG agentplatform.spec.loader - Validating spec against JSON schema" in result.stderr
