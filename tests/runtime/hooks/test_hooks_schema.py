"""Schema tests for the YAML ``hooks:`` section (validation + building)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser

_BASE = """
system:
  name: hooks-test
agents:
  solo:
    description: a solo agent
graph:
  solo:
"""


def _parse(tmp_path: Path, hooks_yaml: str = "") -> object:
    cfg = tmp_path / "agents.yml"
    cfg.write_text(_BASE + hooks_yaml, encoding="utf-8")
    return YAMLParser().parse(str(cfg))


def test_no_hooks_section_is_valid(tmp_path: Path) -> None:
    spec = _parse(tmp_path)
    assert spec.hooks.hooks == ()  # type: ignore[attr-defined]


def test_empty_hooks_section_is_valid(tmp_path: Path) -> None:
    spec = _parse(tmp_path, "hooks: {}\n")
    assert spec.hooks.hooks == ()  # type: ignore[attr-defined]


def test_known_hook_points_build(tmp_path: Path) -> None:
    spec = _parse(
        tmp_path,
        "hooks:\n"
        "  on_run_start:\n"
        "    - ref: company.plugins.auth:attach\n"
        "  before_mcp_request:\n"
        "    - ref: company.plugins.auth:add_headers\n",
    )
    points = {h.point for h in spec.hooks.hooks}  # type: ignore[attr-defined]
    assert points == {"on_run_start", "before_mcp_request"}
    mcp_hook = next(h for h in spec.hooks.hooks if h.point == "before_mcp_request")  # type: ignore[attr-defined]
    assert mcp_hook.failure_policy == "fail"


def test_plugin_method_hook_entry_builds(tmp_path: Path) -> None:
    spec = _parse(
        tmp_path,
        "hooks:\n  before_mcp_request:\n    - plugin: mcp_auth\n      method: before_mcp_request\n",
    )
    hook = spec.hooks.hooks[0]  # type: ignore[attr-defined]
    assert hook.ref is None
    assert hook.plugin == "mcp_auth"
    assert hook.method == "before_mcp_request"


def test_plugin_without_method_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(tmp_path, "hooks:\n  before_mcp_request:\n    - plugin: mcp_auth\n")
    assert "method" in str(exc.value)


def test_method_without_plugin_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(tmp_path, "hooks:\n  before_mcp_request:\n    - method: before_mcp_request\n")
    assert "plugin" in str(exc.value)


def test_ref_with_plugin_method_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(
            tmp_path,
            "hooks:\n"
            "  before_mcp_request:\n"
            "    - ref: m:f\n"
            "      plugin: mcp_auth\n"
            "      method: before_mcp_request\n",
        )
    assert "either 'ref' or 'plugin' + 'method'" in str(exc.value)


def test_unknown_hook_point_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(tmp_path, "hooks:\n  before_everything:\n    - ref: x:y\n")
    assert "Unknown hook point" in str(exc.value)


def test_hook_entry_without_ref_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(tmp_path, "hooks:\n  on_run_start:\n    - failure_policy: warn\n")
    assert "ref" in str(exc.value)


def test_hook_ref_must_be_string(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(tmp_path, "hooks:\n  on_run_start:\n    - ref: 123\n")
    assert "string" in str(exc.value)


def test_hook_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(
            tmp_path,
            "hooks:\n  on_run_start:\n    - ref: m:f\n      config: {audience: internal-mcp}\n",
        )
    assert "config" in str(exc.value)
    assert "Removed field" in str(exc.value)


def test_removed_credential_env_hook_config_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(
            tmp_path,
            "hooks:\n  before_mcp_request:\n    - plugin: mcp_auth\n"
            "      method: before_mcp_request\n"
            "      config:\n        credential_env: INTERNAL_MCP_CREDENTIAL\n",
        )
    assert "Removed field" in str(exc.value)


def test_invalid_failure_policy_fails(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(
            tmp_path,
            "hooks:\n  on_run_start:\n    - ref: m:f\n      failure_policy: maybe\n",
        )
    assert "failure_policy" in str(exc.value)


def test_secret_like_hook_value_rejected(tmp_path: Path) -> None:
    # The existing secret scanner also covers the hooks section: no inline tokens.
    with pytest.raises(ParseError):
        _parse(
            tmp_path,
            "hooks:\n  before_mcp_request:\n    - ref: m:f\n      plugin: api_key\n",
        )


def test_every_hook_point_is_accepted_by_parser(tmp_path: Path) -> None:
    from agent_engine.runtime.hooks.models import HOOK_POINTS

    lines = ["hooks:"]
    for point in HOOK_POINTS:
        lines.append(f"  {point}:")
        lines.append(f"    - ref: company.plugins.x:{point}_hook")
    spec = _parse(tmp_path, "\n".join(lines) + "\n")
    declared = {h.point for h in spec.hooks.hooks}  # type: ignore[attr-defined]
    assert declared == set(HOOK_POINTS)


def test_new_lifecycle_points_are_valid(tmp_path: Path) -> None:
    # The points added for full lifecycle coverage must validate.
    spec = _parse(
        tmp_path,
        "hooks:\n"
        "  on_engine_stop:\n    - ref: m:stop\n"
        "  on_run_end:\n    - ref: m:end\n"
        "  before_tool_call:\n    - ref: m:before\n"
        "  on_tool_error:\n    - ref: m:toolerr\n"
        "  after_mcp_response:\n    - ref: m:resp\n",
    )
    points = {h.point for h in spec.hooks.hooks}  # type: ignore[attr-defined]
    assert points == {
        "on_engine_stop",
        "on_run_end",
        "before_tool_call",
        "on_tool_error",
        "after_mcp_response",
    }
