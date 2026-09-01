"""Normalized tool-result invariants and persistence compatibility."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import ToolMessage

from agent_engine.engine.langgraph.tools.tool_result_normalizer import (
    ProviderToolResultError,
    normalize_tool_result,
)
from agent_engine.runtime.tool_results import (
    NormalizedToolResult,
    ToolResultValidationError,
)


def test_persisted_round_trip_is_json_serializable_and_semantically_equal() -> None:
    result = NormalizedToolResult(
        "Found invoices",
        structured={"z": 1, "invoices": [{"id": "INV-1"}]},
        artifact={"source": "billing"},
    )

    payload = result.to_persisted()
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
    restored = NormalizedToolResult.from_persisted(json.loads(encoded))

    assert restored == result
    assert restored.structured_text() == '{"invoices":[{"id":"INV-1"}],"z":1}'


def test_nested_values_cannot_mutate_the_owned_result() -> None:
    source = {"items": [{"id": "one"}]}
    result = NormalizedToolResult("ok", structured=source)
    source["items"][0]["id"] = "changed"
    exposed = result.structured
    assert isinstance(exposed, dict)
    exposed["items"] = []

    assert result.structured == {"items": [{"id": "one"}]}


@pytest.mark.parametrize(
    "value",
    [
        {1: "non-string key"},
        {"value": object()},
        {"value": float("nan")},
    ],
)
def test_non_json_structured_values_are_rejected(value: object) -> None:
    with pytest.raises(ToolResultValidationError):
        NormalizedToolResult("ok", structured=value)


def test_legacy_text_ledger_value_restores_as_text_only() -> None:
    assert NormalizedToolResult.from_persisted("legacy") == NormalizedToolResult.text_only("legacy")


def test_unknown_persistence_version_is_rejected() -> None:
    with pytest.raises(ToolResultValidationError, match="unsupported"):
        NormalizedToolResult.from_persisted(
            {"version": 99, "text": "future", "structured": None, "artifact": None}
        )


def test_empty_provider_result_has_stable_model_text() -> None:
    result = normalize_tool_result(ToolMessage(content=[], tool_call_id="call-1"))

    assert result == NormalizedToolResult.text_only("[tool returned no content]")


def test_explicit_provider_error_preserves_only_model_safe_text() -> None:
    message = ToolMessage(
        content=[{"type": "text", "text": "account not found"}],
        tool_call_id="call-1",
        status="error",
    )

    with pytest.raises(ProviderToolResultError) as raised:
        normalize_tool_result(message)

    assert raised.value.model_text == "account not found"
    assert "account not found" not in str(raised.value)


def test_artifact_metadata_has_an_aggregate_size_budget() -> None:
    result = normalize_tool_result(
        ToolMessage(
            content="ready",
            tool_call_id="call-1",
            artifact={
                "structured_content": {"report_id": "r-1"},
                "metadata": {f"field-{index}": "x" * 8000 for index in range(20)},
            },
        )
    )

    assert result.text == "ready"
    assert result.structured == {"report_id": "r-1"}
    assert result.artifact is not None
    encoded = json.dumps(result.artifact)
    assert len(encoded) < 65536
    assert encoded.count("x" * 8000) <= 4
    assert '"omitted": true' in encoded
