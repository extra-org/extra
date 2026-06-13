from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_engine.spec import AgentEngineSpec, SpecLoadError, load_spec


def test_examples_agents_yml_validates_successfully() -> None:
    loaded = load_spec(Path("examples/agents.yml"))

    assert isinstance(loaded.spec, AgentEngineSpec)
    assert loaded.spec.system.name == "Rami Levy AI System"


def test_missing_yaml_file_fails_clearly(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yml"

    with pytest.raises(SpecLoadError, match="does not exist"):
        load_spec(missing)


def test_invalid_yaml_syntax_fails_clearly(tmp_path: Path) -> None:
    config = tmp_path / "agent.yml"
    config.write_text("system:\n  name: demo\n  - broken\n", encoding="utf-8")

    with pytest.raises(SpecLoadError, match="Could not parse YAML"):
        load_spec(config)


def write_config(tmp_path: Path, data: dict) -> Path:
    config = tmp_path / "agent.yml"
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config
