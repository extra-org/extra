from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser


def _spec_with_cache_config(cache_val: str | bool | None) -> str:
    if cache_val is not None:
        cache_line = f"      cache_system_prompt: {str(cache_val).lower()}"
    else:
        cache_line = ""
    return f"""
system:
  name: t
orchestrators:
  router:
    description: "Routes requests"
    model:
      provider: anthropic
      name: claude-3-5-sonnet-20240620
{cache_line}
    prompts:
      orchestrator: prompts/r.md
agents:
  agent_one:
    description: "Agent one"
    model:
      provider: anthropic
      name: claude-3-5-sonnet-20240620
{cache_line}
    prompts:
      system: prompts/a.md
graph:
  router:
    agent_one:
"""


def _parse(tmp_path: Path, body: str) -> Any:
    cfg = tmp_path / "spec.yml"
    cfg.write_text(body, encoding="utf-8")
    # Write mock prompt files so validation of files passes
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / "r.md").write_text("router prompt")
    (tmp_path / "prompts" / "a.md").write_text("agent prompt")
    return YAMLParser().parse(str(cfg))


def test_cache_system_prompt_enabled_passes(tmp_path: Path) -> None:
    spec = _parse(tmp_path, _spec_with_cache_config(True))
    assert spec.graph.node.model.cache_system_prompt is True


def test_cache_system_prompt_disabled_passes(tmp_path: Path) -> None:
    spec = _parse(tmp_path, _spec_with_cache_config(False))
    assert spec.graph.node.model.cache_system_prompt is False


def test_cache_system_prompt_defaults_to_true(tmp_path: Path) -> None:
    spec = _parse(tmp_path, _spec_with_cache_config(None))
    assert spec.graph.node.model.cache_system_prompt is True


def test_cache_system_prompt_invalid_type_fails(tmp_path: Path) -> None:
    body = _spec_with_cache_config("not-a-boolean")
    with pytest.raises(ParseError, match="Must be a boolean"):
         _parse(tmp_path, body)
