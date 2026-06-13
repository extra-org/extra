"""build_chat_model maps flat model config to a provider via init_chat_model.

We patch init_chat_model so the test stays offline and asserts the mapping
(provider, name, temperature) rather than constructing a real provider client.
"""

from __future__ import annotations

from typing import Any

import agent_engine.models.factory as factory


def test_passes_provider_name_and_temperature(monkeypatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_init(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "fake-model"

    monkeypatch.setattr(factory, "init_chat_model", fake_init)

    result = factory.build_chat_model(
        provider="anthropic", name="claude-sonnet-4-6", temperature=0.7
    )

    assert result == "fake-model"
    (args, kwargs) = calls[0]
    assert args == ("claude-sonnet-4-6",)
    assert kwargs["model_provider"] == "anthropic"
    assert kwargs["temperature"] == 0.7


def test_omits_temperature_when_unset(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "fake-model"

    monkeypatch.setattr(factory, "init_chat_model", fake_init)

    factory.build_chat_model(provider="openai", name="gpt-x")

    assert captured["model_provider"] == "openai"
    assert "temperature" not in captured
