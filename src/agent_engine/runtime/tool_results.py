"""Provider-independent, persistable tool-result value objects."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias, cast

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
PersistedToolResult: TypeAlias = str | dict[str, object]

_PERSISTENCE_VERSION = 1
_MAX_JSON_DEPTH = 64
_MAX_JSON_VALUES = 10_000
_MAX_JSON_STRING_BYTES = 1_000_000
_MAX_JSON_KEY_BYTES = 1024
_MAX_JSON_TOTAL_KEY_BYTES = 256_000
_MAX_JSON_BYTES = 1_048_576
_MAX_JSON_INTEGER_BITS = 4096
_PERSISTED_KEYS = frozenset({"version", "text", "structured", "artifact"})


class ToolResultValidationError(ValueError):
    """A tool result cannot satisfy Extra's JSON-safe runtime contract."""


@dataclass
class _JsonBudget:
    values_left: int = _MAX_JSON_VALUES
    string_bytes_left: int = _MAX_JSON_STRING_BYTES
    key_bytes_left: int = _MAX_JSON_TOTAL_KEY_BYTES

    def take_value(self, *, path: str) -> None:
        if self.values_left <= 0:
            raise ToolResultValidationError(f"{path} exceeds the JSON value budget")
        self.values_left -= 1

    def take_string(self, value: str, *, path: str) -> None:
        size = _utf8_size(value, path=path)
        if size > self.string_bytes_left:
            raise ToolResultValidationError(f"{path} exceeds the JSON string budget")
        self.string_bytes_left -= size

    def take_key(self, key: str, *, path: str) -> None:
        size = _utf8_size(key, path=path)
        if size > _MAX_JSON_KEY_BYTES:
            raise ToolResultValidationError(f"{path} contains an oversized object key")
        if size > self.key_bytes_left:
            raise ToolResultValidationError(f"{path} exceeds the JSON object-key budget")
        self.key_bytes_left -= size


@dataclass(frozen=True, init=False)
class NormalizedToolResult:
    """One authoritative result for hooks, replay, and model text.

    Nested values are validated and stored as canonical JSON rather than as
    mutable provider-owned objects. Accessors decode fresh values, so callers
    cannot mutate the result held by the execution ledger.
    """

    text: str
    _structured_json: str | None = field(repr=False)
    _artifact_json: str | None = field(repr=False)

    def __init__(
        self,
        text: str,
        structured: object | None = None,
        artifact: object | None = None,
    ) -> None:
        if not isinstance(text, str):
            raise ToolResultValidationError("tool result text must be a string")
        object.__setattr__(self, "text", text)
        object.__setattr__(
            self,
            "_structured_json",
            _canonical_json(structured, field_name="structured result")
            if structured is not None
            else None,
        )
        object.__setattr__(
            self,
            "_artifact_json",
            _canonical_json(artifact, field_name="artifact metadata")
            if artifact is not None
            else None,
        )

    @classmethod
    def text_only(cls, text: str) -> NormalizedToolResult:
        return cls(text=text)

    @property
    def structured(self) -> JsonValue | None:
        return _decode_json(self._structured_json)

    @property
    def artifact(self) -> JsonValue | None:
        return _decode_json(self._artifact_json)

    @property
    def has_structured(self) -> bool:
        return self._structured_json is not None

    @property
    def has_artifact(self) -> bool:
        return self._artifact_json is not None

    def structured_text(self) -> str | None:
        """Return deterministic JSON for a structured-only model fallback."""
        return self._structured_json

    def with_text(self, text: str) -> NormalizedToolResult:
        return NormalizedToolResult(text, self.structured, self.artifact)

    def to_persisted(self) -> dict[str, object]:
        """Return a versioned value containing JSON-serializable primitives."""
        return {
            "version": _PERSISTENCE_VERSION,
            "text": self.text,
            "structured": self.structured,
            "artifact": self.artifact,
        }

    @classmethod
    def from_persisted(
        cls, value: PersistedToolResult | Mapping[str, object]
    ) -> NormalizedToolResult:
        """Restore current payloads and legacy text-only ledger entries."""
        if isinstance(value, str):
            return cls.text_only(value)
        if not isinstance(value, Mapping):
            raise ToolResultValidationError("persisted tool result must be text or a mapping")
        unknown = set(value) - _PERSISTED_KEYS
        if unknown:
            names = ", ".join(sorted(str(key) for key in unknown))
            raise ToolResultValidationError(f"persisted tool result has unknown fields: {names}")
        if value.get("version") != _PERSISTENCE_VERSION:
            raise ToolResultValidationError("unsupported persisted tool result version")
        text = value.get("text")
        if not isinstance(text, str):
            raise ToolResultValidationError("persisted tool result text must be a string")
        return cls(
            text=text,
            structured=value.get("structured"),
            artifact=value.get("artifact"),
        )


def deterministic_json(value: object, *, field_name: str = "tool result") -> str:
    """Serialize one JSON-like value canonically or raise a typed error."""
    return _canonical_json(value, field_name=field_name)


def _canonical_json(value: object, *, field_name: str) -> str:
    normalized = _json_value(
        value,
        path=field_name,
        depth=0,
        budget=_JsonBudget(),
    )
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if _utf8_size(encoded, path=field_name) > _MAX_JSON_BYTES:
        raise ToolResultValidationError(f"{field_name} exceeds the encoded JSON budget")
    return encoded


def _json_value(
    value: object,
    *,
    path: str,
    depth: int,
    budget: _JsonBudget,
) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ToolResultValidationError(f"{path} exceeds maximum nesting depth")
    budget.take_value(path=path)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        budget.take_string(value, path=path)
        return value
    if isinstance(value, int):
        if value.bit_length() > _MAX_JSON_INTEGER_BITS:
            raise ToolResultValidationError(f"{path} contains an oversized integer")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolResultValidationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ToolResultValidationError(f"{path} contains a non-string object key")
            budget.take_key(key, path=path)
            normalized[key] = _json_value(
                item,
                path=f"{path}.<key>",
                depth=depth + 1,
                budget=budget,
            )
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _json_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                budget=budget,
            )
            for index, item in enumerate(value)
        ]
    raise ToolResultValidationError(
        f"{path} contains unsupported value type {type(value).__name__}"
    )


def _decode_json(value: str | None) -> JsonValue | None:
    if value is None:
        return None
    return cast(JsonValue, json.loads(value))


def _utf8_size(value: str, *, path: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ToolResultValidationError(f"{path} contains invalid Unicode") from exc
