from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_engine.spec import SpecSchemaError, load_spec


def test_json_schema_violation_fails_clearly(tmp_path: Path) -> None:
    config = tmp_path / "agent.yml"
    config.write_text(yaml.safe_dump({"graph": {"root": None}}), encoding="utf-8")

    with pytest.raises(SpecSchemaError) as exc_info:
        load_spec(config)

    assert "$" in str(exc_info.value)
    assert "system" in str(exc_info.value)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "agent.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "system": {"name": "demo"},
                "graph": {"root": None},
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SpecSchemaError, match="Additional properties"):
        load_spec(config)
