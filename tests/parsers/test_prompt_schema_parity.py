"""The published JSON Schema and the YAML parser must agree on ``prompts`` keys.

``examples/config.schema.json`` is what editors and external tooling validate
against; ``YAMLParser`` is what the engine enforces. A key the parser accepts
but the schema rejects (or vice versa) makes documented config look invalid,
so each case here is checked against both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "examples" / "config.schema.json"

_TEMPLATE = """
system:
  name: test
orchestrators:
  router:
    description: "Router"
    prompts:
      orchestrator: prompts/router.md
{router_extra}
agents:
  worker:
    description: "Worker"
    prompts:
      system: prompts/worker.md
{worker_extra}
graph:
  router:
    worker:
"""


def _schema_accepts(document: dict[str, Any]) -> bool:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.validators.validator_for(schema)(schema)
    return not list(validator.iter_errors(document))


def _parser_accepts(tmp_path: Path, body: str) -> bool:
    cfg = tmp_path / "spec.yml"
    cfg.write_text(body, encoding="utf-8")
    try:
        YAMLParser().parse(str(cfg))
    except ParseError:
        return False
    return True


@pytest.mark.parametrize(
    ("router_extra", "worker_extra", "valid"),
    [
        pytest.param("", "", True, id="omitted"),
        pytest.param("      extra_default: true", "", True, id="orchestrator-true"),
        pytest.param("      extra_default: false", "", True, id="orchestrator-false"),
        pytest.param("", "      extra_default: true", True, id="agent-true"),
        pytest.param("", "      extra_default: false", True, id="agent-false"),
        pytest.param('      extra_default: "no"', "", False, id="orchestrator-string"),
        pytest.param("", "      extra_default: 0", False, id="agent-integer"),
    ],
)
def test_schema_and_parser_agree_on_extra_default(
    tmp_path: Path, router_extra: str, worker_extra: str, valid: bool
) -> None:
    body = _TEMPLATE.format(router_extra=router_extra, worker_extra=worker_extra)

    assert _parser_accepts(tmp_path, body) is valid
    assert _schema_accepts(yaml.safe_load(body)) is valid
