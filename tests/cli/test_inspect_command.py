"""Tests for the offline ``agentctl inspect`` command / diagnostics."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from agentctl.diagnostics import inspect_spec
from agentctl.main import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples"
FLAGSHIP = "enterprise-knowledge-assistant/agents.yaml"


def _ex(name: str) -> str:
    return str(EXAMPLES / name)


def _write(tmp_path: Path, body: str) -> str:
    cfg = tmp_path / "spec.yml"
    cfg.write_text(body, encoding="utf-8")
    return str(cfg)


def test_inspect_shows_mcp_server_ids(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [remote]}}\ngraph: {a: }\n"
        "mcps: {remote: {url: 'https://mcp.example.com/mcp'}}\n",
    )
    out = inspect_spec(spec)
    assert "mcp_servers: 1" in out
    assert "- remote" in out
    assert "url: https://mcp.example.com/mcp" in out


def test_inspect_shows_tool_tags(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [bc]}}\ngraph: {a: }\n"
        "mcps: {bc: {url: 'https://x/mcp', tool_tags: ['policies']}}\n",
    )
    out = inspect_spec(spec)
    assert "tool_tags: policies" in out


def test_inspect_shows_default_header_transport(tmp_path: Path) -> None:
    # No explicit transport -> the default header is shown.
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [bc]}}\ngraph: {a: }\n"
        "mcps: {bc: {url: 'https://x/mcp', tool_tags: ['policies']}}\n",
    )
    out = inspect_spec(spec)
    assert "tool_tag_transport: header X-MCP-Tool-Tag (default)" in out


def test_inspect_shows_query_param_override(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [bc]}}\ngraph: {a: }\n"
        "mcps:\n  bc:\n    url: 'https://x/mcp'\n    tool_tags: ['docs']\n"
        "    tool_tag_transport: {type: query_param, param_name: tag}\n",
    )
    out = inspect_spec(spec)
    assert "tool_tag_transport: query_param tag (override)" in out


def test_inspect_shows_managed_hook_points() -> None:
    # The flagship declares managed hooks (plugin + method resolved via manifest).
    out = inspect_spec(_ex(FLAGSHIP))
    assert "hooks: 5" in out
    assert "plugin=research_hooks method=validate_environment" in out


def test_inspect_shows_hook_without_config(tmp_path: Path) -> None:
    spec = tmp_path / "spec.yml"
    spec.write_text(
        "system: {name: t}\nagents: {a: {description: d}}\ngraph: {a: }\n"
        "hooks:\n  on_run_start:\n    - ref: company.x:f\n",
        encoding="utf-8",
    )
    out = inspect_spec(str(spec))
    assert "on_run_start: ref=company.x:f" in out
    assert "config_keys" not in out


def test_inspect_handles_no_hooks_and_no_tags(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "system: {name: t}\nagents: {a: {description: d, mcps: [remote]}}\ngraph: {a: }\n"
        "mcps: {remote: {url: 'https://mcp.example.com/mcp'}}\n",
    )
    out = inspect_spec(spec)
    assert "hooks: 0" in out
    assert "tool_tags: (none)" in out
    assert "no hooks configured" in out


def test_inspect_shows_import_roots_resolved() -> None:
    # The flagship declares `import_roots: ["."]`, resolved relative to its spec.
    out = inspect_spec(_ex(FLAGSHIP))
    assert "import_roots:" in out
    assert "enterprise-knowledge-assistant" in out


def test_inspect_shows_plugins_manifest() -> None:
    out = inspect_spec(_ex(FLAGSHIP))
    assert "plugins_manifest:" in out
    assert "exists: true" in out


def test_cli_inspect_exit_zero() -> None:
    runner = CliRunner()
    res = runner.invoke(cli, ["--log-level", "WARNING", "inspect", _ex(FLAGSHIP)])
    assert res.exit_code == 0
    assert "system: Enterprise Knowledge Assistant" in res.output


def test_cli_inspect_missing_file_exits_nonzero() -> None:
    runner = CliRunner()
    res = runner.invoke(cli, ["--log-level", "WARNING", "inspect", "/no/such/spec.yml"])
    assert res.exit_code != 0


def test_cli_has_all_commands() -> None:
    # Regression: existing commands remain registered alongside validate/inspect.
    assert {"validate", "inspect", "run", "generate", "serve"} <= set(cli.commands)
