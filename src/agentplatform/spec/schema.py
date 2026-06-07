"""JSON schema validation for raw YAML configuration data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from agentplatform.spec.errors import SpecLoadError, SpecSchemaError, ValidationIssue

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "examples" / "config.schema.json"


def load_json_schema(path: Path = DEFAULT_SCHEMA_PATH) -> dict[str, Any]:
    """Load the JSON schema used as the external YAML contract."""
    try:
        with path.open(encoding="utf-8") as schema_file:
            loaded = json.load(schema_file)
    except OSError as exc:
        raise SpecLoadError(f"Could not read JSON schema {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecLoadError(f"Could not parse JSON schema {path}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise SpecLoadError(f"JSON schema {path} must contain an object")
    return loaded


def validate_json_schema(data: object, schema_path: Path = DEFAULT_SCHEMA_PATH) -> None:
    """Validate raw YAML data against the JSON schema."""
    schema = load_json_schema(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(data), key=_error_sort_key)
    if errors:
        raise SpecSchemaError([_issue_from_error(error) for error in errors])


def _error_sort_key(error: ValidationError) -> tuple[str, str]:
    return (_json_path(list(error.absolute_path)), error.message)


def _issue_from_error(error: ValidationError) -> ValidationIssue:
    return ValidationIssue(path=_json_path(list(error.absolute_path)), message=error.message)


def _json_path(parts: list[object]) -> str:
    if not parts:
        return "$"
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path
