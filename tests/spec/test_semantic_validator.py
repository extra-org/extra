from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from agentplatform.spec import SpecSchemaError, SpecValidationError, load_spec


def minimal_valid_config() -> dict[str, Any]:
    return {
        "system": {"name": "demo"},
        "resolvers": ["current_date"],
        "tools": {
            "count_words": {"description": "Count words in a text string"},
        },
        "mcps": {
            "local_mcp": {"url": "https://example.com/mcp/sse"},
        },
        "orchestrators": {
            "root": {
                "description": "Route requests.",
                "prompts": {"orchestrator": "prompts/root/orchestrator.md"},
            }
        },
        "agents": {
            "worker": {
                "description": "Do work.",
                "prompts": {"system": "prompts/worker/system.md"},
                "resolvers": ["current_date"],
                "tools": ["count_words"],
                "mcps": ["local_mcp"],
            }
        },
        "graph": {"root": {"worker": None}},
    }


def write_valid_files(tmp_path: Path) -> None:
    (tmp_path / "prompts" / "root").mkdir(parents=True)
    (tmp_path / "prompts" / "root" / "orchestrator.md").write_text(
        "Route requests.",
        encoding="utf-8",
    )
    (tmp_path / "prompts" / "worker").mkdir(parents=True)
    (tmp_path / "prompts" / "worker" / "system.md").write_text(
        "Do work.",
        encoding="utf-8",
    )


def write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    config = tmp_path / "agent.yml"
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config


def load_tmp_config(tmp_path: Path, data: dict[str, Any]):
    write_valid_files(tmp_path)
    return load_spec(write_config(tmp_path, data))


def test_minimal_valid_config_loads(tmp_path: Path) -> None:
    loaded = load_tmp_config(tmp_path, minimal_valid_config())

    assert loaded.spec.system.name == "demo"


def test_graph_references_unknown_node(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["graph"] = {"root": {"missing_agent": None}}

    with pytest.raises(SpecValidationError, match="Unknown graph node 'missing_agent'"):
        load_tmp_config(tmp_path, data)


def test_graph_requires_single_root(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["graph"] = {"root": None, "worker": None}

    with pytest.raises(SpecValidationError, match="exactly one root"):
        load_tmp_config(tmp_path, data)


def test_graph_cycle_is_rejected(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["graph"] = {"root": {"worker": {"root": None}}}

    with pytest.raises(SpecValidationError, match="Graph cycle detected"):
        load_tmp_config(tmp_path, data)


def test_unknown_resolver_reference_fails(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["agents"]["worker"]["resolvers"] = ["missing_resolver"]

    with pytest.raises(SpecValidationError, match="Unknown resolver reference"):
        load_tmp_config(tmp_path, data)


def test_invalid_resolver_scope_fails(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["resolvers"] = {"current_date": {"scope": "global"}}

    with pytest.raises(SpecSchemaError, match="Invalid resolver scope 'global'"):
        load_tmp_config(tmp_path, data)


def test_scoped_resolver_mapping_loads(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["resolvers"] = {"current_date": {"scope": "shared", "return_type": "str"}}

    loaded = load_tmp_config(tmp_path, data)

    assert loaded.spec.resolvers["current_date"].scope == "shared"


def test_unknown_tool_reference_fails(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["agents"]["worker"]["tools"] = ["missing_tool"]

    with pytest.raises(SpecValidationError, match="Unknown tool reference"):
        load_tmp_config(tmp_path, data)


def test_unknown_mcp_reference_fails(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["agents"]["worker"]["mcps"] = ["missing_mcp"]

    with pytest.raises(SpecValidationError, match="Unknown MCP reference"):
        load_tmp_config(tmp_path, data)


def test_missing_prompt_path_fails(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["agents"]["worker"]["prompts"]["system"] = "prompts/missing/system.md"

    with pytest.raises(SpecValidationError, match="Prompt file does not exist"):
        load_tmp_config(tmp_path, data)


def test_hardcoded_secret_is_rejected(tmp_path: Path) -> None:
    data = minimal_valid_config()
    # A resolver id that looks like a secret should be rejected.
    data["resolvers"].append("api_key_lookup")

    with pytest.raises(SpecValidationError, match="Hardcoded secret-like"):
        load_tmp_config(tmp_path, data)


def test_protected_agent_requires_access_plugin_file(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["agents"]["worker"]["protected"] = True

    with pytest.raises(SpecValidationError, match=r"Protected nodes require plugins/access\.py"):
        load_tmp_config(tmp_path, data)


def test_protected_agent_checks_access_plugin_existence_without_importing(tmp_path: Path) -> None:
    data = minimal_valid_config()
    data["agents"]["worker"]["protected"] = True
    write_valid_files(tmp_path)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "access.py").write_text(
        "raise RuntimeError('validation must not import this file')\n",
        encoding="utf-8",
    )

    loaded = load_spec(write_config(tmp_path, data))

    assert loaded.spec.agents["worker"].protected is True
