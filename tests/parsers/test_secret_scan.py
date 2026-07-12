"""Tests for the parser's hardcoded-secret scan.

The scan must block actual credentials (secret-named keys, inline
assignments, well-known key shapes) without rejecting ordinary prose that
merely mentions words like "password" or "token" — e.g. an agent whose
description is "Handles password reset requests".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser


def _spec_with_description(description: str) -> str:
    return f"""
system:
  name: t
orchestrators:
  router:
    description: "Routes requests"
    prompts:
      orchestrator: prompts/r.md
agents:
  reset_agent:
    description: "{description}"
    prompts:
      system: prompts/a.md
graph:
  router:
    reset_agent:
"""


def _parse(tmp_path: Path, body: str) -> None:
    cfg = tmp_path / "spec.yml"
    cfg.write_text(body, encoding="utf-8")
    YAMLParser().parse(str(cfg))


# -- prose mentioning secret words must pass ---------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "Handles password reset requests for customers",
        "Explains token bucket rate limiting",
        "Answers questions about API key rotation policies",
        "Keeps customer secrets confidential",
    ],
)
def test_prose_mentioning_secret_words_is_allowed(tmp_path: Path, description: str) -> None:
    _parse(tmp_path, _spec_with_description(description))  # must not raise


# -- actual credentials must still be rejected -------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "use api_key=supersecret123 for the backend",
        "password: hunter2secret",
        "sk-abc123def456ghij789",  # OpenAI/Anthropic-style key shape
        "AKIAIOSFODNN7EXAMPLE",  # AWS access key id shape
        "ghp_abcdefghij0123456789klmnopqrstuv",  # GitHub PAT shape
        "xoxb-1234567890-abcdefghijklm",  # Slack token shape
    ],
)
def test_credential_like_values_are_rejected(tmp_path: Path, description: str) -> None:
    with pytest.raises(ParseError, match="secret-like value"):
        _parse(tmp_path, _spec_with_description(description))


def test_secret_named_keys_are_still_rejected(tmp_path: Path) -> None:
    body = """
system:
  name: t
mcps:
  orders:
    url: "https://mcp.example.com/mcp"
    api_key: "value"
agents:
  a:
    description: "agent"
    prompts:
      system: prompts/a.md
graph:
  a:
"""
    with pytest.raises(ParseError, match="secret-like key"):
        _parse(tmp_path, body)
