"""Tests for the offline ``agentctl validate`` command / diagnostics."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_engine.parsers.yaml.parser import YAMLParser
from agentctl.diagnostics import validate_spec
from agentctl.main import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples"


@pytest.fixture(autouse=True)
def _restore_sys() -> Iterator[None]:
    """validate registers plugin import roots on sys.path; restore after each test."""
    path_before = list(sys.path)
    mods_before = set(sys.modules)
    try:
        yield
    finally:
        sys.path[:] = path_before
        for name in set(sys.modules) - mods_before:
            sys.modules.pop(name, None)


def _ex(name: str) -> str:
    return str(EXAMPLES / name)


def _write(tmp_path: Path, body: str) -> str:
    cfg = tmp_path / "spec.yml"
    cfg.write_text(body, encoding="utf-8")
    return str(cfg)


# -- passing examples --------------------------------------------------------


def test_validate_reports_tool_tags(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [bc]}}\ngraph: {a: }\n"
        "mcps: {bc: {url: 'https://x/mcp', tool_tags: ['policies']}}\n",
    )
    result = validate_spec(spec)
    assert result.ok, result.errors
    assert result.tool_tags == {"bc": ("policies",)}


def test_validate_passes_on_enterprise_knowledge_assistant_demo() -> None:
    """Smoke test for the richest example (`examples/enterprise-knowledge-assistant/agents.yaml`).

    This is the Enterprise Knowledge Assistant reference demo — multi-level
    orchestration, remote + authenticated MCPs, local tools, shared/agent-scoped
    resolvers, and all five hook lifecycle points. Nothing else in the test
    suite parses it, so it can silently drift out of sync with the schema. This
    is intentionally the same offline check `agentctl validate` runs: no LLM
    calls, no MCP network, no tool execution — hooks are imported/instantiated
    but their methods are never invoked.
    """
    result = validate_spec(_ex("enterprise-knowledge-assistant/agents.yaml"))
    assert result.ok, result.errors
    assert result.agents == 5
    assert result.mcp_servers == 2
    assert result.hooks == 5
    assert result.import_roots  # "." resolved relative to the spec file


def test_no_hook_spec_does_not_require_plugins_toml(tmp_path: Path) -> None:
    # A no-hook spec with no plugins/ dir at all must still validate.
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d}}\ngraph: {a: }\n",
    )
    result = validate_spec(spec)
    assert result.ok, result.errors
    assert not (tmp_path / "plugins" / "plugins.toml").exists()


def test_validate_offline_for_unreachable_mcp(tmp_path: Path) -> None:
    # An obviously unreachable MCP URL still validates — no network is touched.
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [bc]}}\n"
        "graph: {a: }\nmcps: {bc: {url: 'http://127.0.0.1:1/mcp'}}\n",
    )
    result = validate_spec(spec)
    assert result.ok, result.errors
    assert result.mcp_servers == 1


def test_validate_accepts_bedrock_model_config(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\n"
        "defaults:\n"
        "  model:\n"
        "    provider: bedrock\n"
        "    name: anthropic.claude-3-5-haiku-20241022-v1:0\n"
        "    region: us-east-1\n"
        "    temperature: 0.0\n"
        "    max_tokens: 512\n"
        "    top_p: 0.8\n"
        "agents: {a: {description: d}}\n"
        "graph: {a: }\n",
    )

    result = validate_spec(spec)

    assert result.ok, result.errors


def test_validate_accepts_gemini_model_config(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\n"
        "defaults:\n"
        "  model:\n"
        "    provider: gemini\n"
        "    name: gemini-2.5-flash\n"
        "    temperature: 0.2\n"
        "    max_tokens: 1024\n"
        "    top_p: 0.9\n"
        "agents: {a: {description: d}}\n"
        "graph: {a: }\n",
    )

    result = validate_spec(spec)

    assert result.ok, result.errors


def test_yaml_parser_preserves_bedrock_model_fields(tmp_path: Path) -> None:
    spec_path = _write(
        tmp_path,
        "system: {name: t}\n"
        "defaults:\n"
        "  model:\n"
        "    provider: bedrock\n"
        "    name: anthropic.claude-3-5-haiku-20241022-v1:0\n"
        "    region: us-east-1\n"
        "    temperature: 0.0\n"
        "    max_tokens: 512\n"
        "    top_p: 0.8\n"
        "agents: {a: {description: d}}\n"
        "graph: {a: }\n",
    )

    spec = YAMLParser().parse(spec_path)

    model = spec.graph.node.model
    assert model.provider == "bedrock"
    assert model.name == "anthropic.claude-3-5-haiku-20241022-v1:0"
    assert model.region == "us-east-1"
    assert model.temperature == 0.0
    assert model.max_tokens == 512
    assert model.top_p == 0.8


def test_validate_rejects_unsupported_model_provider(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\n"
        "defaults: {model: {provider: openai, name: gpt-4o-mini}}\n"
        "agents: {a: {description: d}}\n"
        "graph: {a: }\n",
    )

    result = validate_spec(spec)

    assert not result.ok
    assert any("provider" in error and "bedrock" in error for error in result.errors)


# -- failing specs -----------------------------------------------------------

def test_validate_rejects_negative_temperature(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\n"
        "defaults: {model: {provider: anthropic, name: claude-haiku-4-5, temperature: -0.5}}\n"
        "agents: {a: {description: d}}\n"
        "graph: {a: }\n",
    )
    result = validate_spec(spec)
    assert not result.ok
    assert any("temperature" in e for e in result.errors)


def test_validate_rejects_non_numeric_temperature(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\n"
        "defaults: {model: {provider: anthropic, name: claude-haiku-4-5, temperature: hot}}\n"
        "agents: {a: {description: d}}\n"
        "graph: {a: }\n",
    )
    result = validate_spec(spec)
    assert not result.ok
    assert any("temperature" in e for e in result.errors)

def test_validate_fails_on_invalid_tool_tags(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [bc]}}\ngraph: {a: }\n"
        "mcps: {bc: {url: 'https://x/mcp', tool_tags: ['']}}\n",
    )
    result = validate_spec(spec)
    assert not result.ok
    assert any("tool_tags" in e for e in result.errors)


def test_validate_fails_on_missing_import_root(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d}}\ngraph: {a: }\n"
        "plugins: {import_roots: ['does_not_exist']}\n",
    )
    result = validate_spec(spec)
    assert not result.ok
    assert any("import root not found" in e for e in result.errors)


def test_validate_fails_on_plugin_hook_not_in_manifest(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d}}\ngraph: {a: }\n"
        "hooks: {before_mcp_request: [{plugin: nope, method: x}]}\n",
    )
    result = validate_spec(spec)
    assert not result.ok
    assert any("hooks:" in e and "nope" in e for e in result.errors)


def test_validate_fails_on_missing_file() -> None:
    result = validate_spec("/no/such/spec.yml")
    assert not result.ok
    assert any("not found" in e for e in result.errors)


# -- CLI wiring (exit codes) -------------------------------------------------


def test_cli_validate_exit_zero() -> None:
    runner = CliRunner()
    res = runner.invoke(
        cli,
        ["--log-level", "WARNING", "validate", _ex("enterprise-knowledge-assistant/agents.yaml")],
    )
    assert res.exit_code == 0
    assert "validation passed" in res.output


def test_cli_validate_exit_nonzero_on_failure(tmp_path: Path) -> None:
    spec = _write(tmp_path, "system: {name: t}\n")  # missing graph -> invalid
    runner = CliRunner()
    res = runner.invoke(cli, ["--log-level", "WARNING", "validate", spec])
    assert res.exit_code != 0
    assert "Validation failed" in res.output
