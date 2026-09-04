"""LangChain tool-result adapter into Extra's runtime contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from langchain_core.messages import ToolMessage

from agent_engine.runtime.tool_results import (
    NormalizedToolResult,
    ToolResultValidationError,
    deterministic_json,
)

_STRUCTURED_KEYS = ("structured_content", "structuredContent")
_EMPTY_RESULT_TEXT = "[tool returned no content]"
_MAX_ARTIFACT_DEPTH = 8
_MAX_ARTIFACT_ITEMS = 128
_MAX_ARTIFACT_TOTAL_VALUES = 1024
_MAX_ARTIFACT_STRING_CHARS = 8192
_MAX_ARTIFACT_TOTAL_STRING_CHARS = 32768
_MAX_ARTIFACT_KEY_CHARS = 256
_MAX_ARTIFACT_JSON_CHARS = 65536


class ToolResultNormalizationError(RuntimeError):
    """A provider result does not satisfy Extra's tool-result contract."""


class ProviderToolResultError(ToolResultNormalizationError):
    """The provider returned an explicit error result instead of raising."""

    def __init__(self, model_text: str) -> None:
        self.model_text = model_text
        super().__init__("tool provider returned an error result")


@dataclass
class _ArtifactBudget:
    values_left: int = _MAX_ARTIFACT_TOTAL_VALUES
    string_chars_left: int = _MAX_ARTIFACT_TOTAL_STRING_CHARS

    def take_value(self) -> bool:
        if self.values_left <= 0:
            return False
        self.values_left -= 1
        return True

    def take_string(self, size: int) -> bool:
        if size > self.string_chars_left:
            return False
        self.string_chars_left -= size
        return True


def normalize_tool_result(result: object) -> NormalizedToolResult:
    """Normalize one LangChain or direct local-tool result exactly once."""
    try:
        if isinstance(result, ToolMessage):
            model_text, content_structured = _tool_message_content(result.content)
            artifact_structured, artifact = _split_artifact(result.artifact)
            structured = (
                artifact_structured if artifact_structured is not None else content_structured
            )
            normalized = NormalizedToolResult(model_text, structured, artifact)
            normalized = _with_model_fallback(normalized)
            if result.status == "error":
                raise ProviderToolResultError(normalized.text)
            return normalized

        model_text, structured = _direct_result(result)
        return _with_model_fallback(NormalizedToolResult(model_text, structured))
    except ProviderToolResultError:
        raise
    except ToolResultValidationError as exc:
        raise ToolResultNormalizationError(str(exc)) from exc


def _tool_message_content(content: object) -> tuple[str, object | None]:
    if isinstance(content, str):
        return content, _json_container(content)
    if isinstance(content, list):
        return "\n".join(_content_block_text(block) for block in content), None
    raise ToolResultNormalizationError(
        f"LangChain ToolMessage content has unsupported type {type(content).__name__}"
    )


def _direct_result(result: object) -> tuple[str, object | None]:
    if isinstance(result, str):
        return result, _json_container(result)
    if isinstance(result, (Mapping, list, tuple)):
        return deterministic_json(result), result
    if result is None or isinstance(result, (bool, int, float)):
        return deterministic_json(result), result
    raise ToolResultNormalizationError(
        f"tool returned unsupported result type {type(result).__name__}"
    )


def _content_block_text(block: object) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, Mapping):
        raise ToolResultNormalizationError(
            f"tool content block has unsupported type {type(block).__name__}"
        )
    block_type = block.get("type")
    if not isinstance(block_type, str) or not block_type:
        raise ToolResultNormalizationError("tool content block must declare a string type")
    if block_type == "text":
        text = block.get("text")
        if not isinstance(text, str):
            raise ToolResultNormalizationError("text content block must contain string text")
        return text
    return f"[unsupported {block_type} block]"


def _json_container(content: str) -> object | None:
    """Recover dict/list local results serialized by LangChain ToolMessage."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _with_model_fallback(result: NormalizedToolResult) -> NormalizedToolResult:
    if result.text:
        return result
    structured_text = result.structured_text()
    return result.with_text(structured_text or _EMPTY_RESULT_TEXT)


def _split_artifact(artifact: object) -> tuple[object | None, object | None]:
    if artifact is None:
        return None, None
    if not isinstance(artifact, Mapping):
        raise ToolResultNormalizationError("tool artifact must be a mapping")

    present_keys = [key for key in _STRUCTURED_KEYS if key in artifact]
    if len(present_keys) > 1:
        raise ToolResultNormalizationError("tool artifact declares structured output twice")
    structured = artifact[present_keys[0]] if present_keys else None
    metadata: dict[str, object] = {}
    budget = _ArtifactBudget()
    for key, value in artifact.items():
        if key in _STRUCTURED_KEYS:
            continue
        name = _string_key(key, path="artifact")
        metadata[name] = _bounded_artifact_value(
            value,
            path="artifact.<key>",
            depth=0,
            budget=budget,
        )
    if metadata:
        encoded = deterministic_json(metadata, field_name="artifact metadata")
        if len(encoded) > _MAX_ARTIFACT_JSON_CHARS:
            metadata = {
                "type": "artifact_metadata",
                "size": len(encoded),
                "omitted": True,
            }
    return structured, metadata or None


def _string_key(key: object, *, path: str) -> str:
    if not isinstance(key, str):
        raise ToolResultNormalizationError(f"{path} contains a non-string key")
    if len(key) > _MAX_ARTIFACT_KEY_CHARS:
        raise ToolResultNormalizationError(f"{path} contains an oversized key")
    return key


def _bounded_artifact_value(
    value: object,
    *,
    path: str,
    depth: int,
    budget: _ArtifactBudget,
) -> object:
    """Keep bounded JSON metadata and replace known large payload shapes."""
    if not budget.take_value():
        return {"type": "value", "omitted": True}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "binary", "size": len(value), "omitted": True}
    if isinstance(value, str):
        if len(value) > _MAX_ARTIFACT_STRING_CHARS or not budget.take_string(len(value)):
            return {"type": "text", "size": len(value), "omitted": True}
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= _MAX_ARTIFACT_DEPTH:
        return {"type": "nested", "omitted": True}
    if isinstance(value, Mapping):
        if len(value) > _MAX_ARTIFACT_ITEMS:
            return {"type": "object", "size": len(value), "omitted": True}
        nested: dict[str, object] = {}
        for key, item in value.items():
            name = _string_key(key, path=path)
            nested[name] = _bounded_artifact_value(
                item,
                path=f"{path}.<key>",
                depth=depth + 1,
                budget=budget,
            )
        return nested
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_ARTIFACT_ITEMS:
            return {"type": "array", "size": len(value), "omitted": True}
        return [
            _bounded_artifact_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                budget=budget,
            )
            for index, item in enumerate(value)
        ]
    raise ToolResultNormalizationError(
        f"{path} contains unsupported value type {type(value).__name__}"
    )
