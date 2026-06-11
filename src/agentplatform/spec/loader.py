"""Safe YAML loading into typed specification models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentplatform.spec.errors import SpecLoadError, SpecSchemaError, ValidationIssue
from agentplatform.spec.models import AgentEngineSpec
from agentplatform.spec.schema import validate_json_schema
from agentplatform.spec.validator import validate_spec


@dataclass(frozen=True)
class LoadedSpec:
    """A validated spec and its source location."""

    spec: AgentEngineSpec
    source_path: Path


def load_yaml_data(path: Path | str) -> dict[str, Any]:
    """Read and safely parse YAML into plain data."""
    source_path = Path(path)
    if not source_path.is_file():
        raise SpecLoadError(f"YAML config file does not exist: {source_path}")

    try:
        raw_text = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecLoadError(f"Could not read YAML config {source_path}: {exc}") from exc

    try:
        loaded = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SpecLoadError(f"Could not parse YAML config {source_path}: {exc}") from exc

    if loaded is None:
        raise SpecLoadError(f"YAML config is empty: {source_path}")
    if not isinstance(loaded, dict):
        raise SpecLoadError(f"YAML config root must be a mapping: {source_path}")
    return loaded


def load_spec(path: Path | str) -> LoadedSpec:
    """Load, schema-validate, model-validate, and semantically validate a spec."""
    source_path = Path(path).resolve()
    data = load_yaml_data(source_path)
    validate_json_schema(data)
    try:
        spec = AgentEngineSpec.model_validate(data)
    except ValidationError as exc:
        raise SpecSchemaError(
            [
                ValidationIssue(
                    path=_pydantic_path(issue["loc"]),
                    message=str(issue["msg"]),
                )
                for issue in exc.errors()
            ]
        ) from exc
    validate_spec(spec, base_dir=source_path.parent)
    return LoadedSpec(spec=spec, source_path=source_path)


def _pydantic_path(location: tuple[int | str, ...]) -> str:
    if not location:
        return "$"
    path = "$"
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path
