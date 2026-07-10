"""Tests for the ``agentctl serve`` command's port defaulting/override.

The real ``create_app`` and ``uvicorn.run`` are stubbed out — these tests only
assert how the CLI resolves the bind port, not that a server actually starts.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from agentctl.main import cli

DEFAULT_PORT = 8090


@pytest.fixture
def captured_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub the serve command's side effects and capture what uvicorn.run gets."""
    import uvicorn

    captured: dict[str, object] = {}

    def fake_run(app: object, host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("agentctl.main.load_env", lambda config, env: None)
    monkeypatch.setattr("agent_engine.api.app.create_app", lambda config: object())
    monkeypatch.setattr(uvicorn, "run", fake_run)
    return captured


def test_serve_defaults_to_8090(captured_run: dict[str, object]) -> None:
    res = CliRunner().invoke(cli, ["serve", "--config", "agents.yml"])
    assert res.exit_code == 0, res.output
    assert captured_run["port"] == DEFAULT_PORT


def test_serve_port_flag_overrides_default(captured_run: dict[str, object]) -> None:
    res = CliRunner().invoke(cli, ["serve", "--config", "agents.yml", "--port", "8080"])
    assert res.exit_code == 0, res.output
    assert captured_run["port"] == 8080


def test_serve_port_env_var_overrides_default(captured_run: dict[str, object]) -> None:
    res = CliRunner().invoke(
        cli, ["serve", "--config", "agents.yml"], env={"PORT": "9000"}
    )
    assert res.exit_code == 0, res.output
    assert captured_run["port"] == 9000
