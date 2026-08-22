"""LangGraphEngine.complete() — the stateless side of TextCompletionEngine.

No graph, no history, no persistence: this exercises only the model-building
and message-shaping that titling (and anything else the capability serves in
the future) depends on.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_engine.core.spec import DefaultsConfig, ModelConfig
from agent_engine.engine.langgraph.engine import LangGraphEngine
from tests.fixtures.utils import FakeChatModel, load_test_system


class RecordingChatModel(FakeChatModel):
    def __init__(self, answer: str = "A Generated Title") -> None:
        super().__init__(answer)
        self.invocations: list[tuple[list[Any], Any]] = []

    async def ainvoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> AIMessage:
        self.invocations.append((messages, config))
        return await super().ainvoke(messages, config, **kwargs)


class RecordingModelFactory:
    """Records every (provider, name, temperature, kwargs) it's asked to build."""

    def __init__(self, model: RecordingChatModel) -> None:
        self._model = model
        self.calls: list[tuple[str, str, float | None, dict[str, Any]]] = []

    def __call__(
        self, provider: str, name: str, temperature: float | None = None, **kwargs: Any
    ) -> Any:
        self.calls.append((provider, name, temperature, kwargs))
        return self._model


async def test_cannot_complete_text_before_build(tmp_path: Path) -> None:
    engine = LangGraphEngine(tmp_path)
    assert not engine.can_complete_text


async def test_complete_without_a_default_model_raises(tmp_path: Path) -> None:
    engine = LangGraphEngine(tmp_path)
    with pytest.raises(RuntimeError):
        await engine.complete("hello")


async def test_complete_forwards_the_full_default_model() -> None:
    """Region, temperature, and top_p travel with the model, not just its name.

    A Bedrock deployment's `defaults.model` carries the region its agents
    already need; `complete()` must use the same config, not a subset of it.
    """
    spec, base_dir = load_test_system()
    rich_model = ModelConfig(
        provider="fake",
        name="fake-utility-model",
        temperature=0.4,
        region="us-east-1",
        top_p=0.8,
        max_tokens=999,
    )
    spec = dataclasses.replace(spec, defaults=DefaultsConfig(model=rich_model))
    chat_model = RecordingChatModel()
    factory = RecordingModelFactory(chat_model)

    async with LangGraphEngine(base_dir, model_factory=factory) as engine:
        await engine.build(spec)
        assert engine.can_complete_text

        result = await engine.complete("hello", max_tokens=24)

    assert result == "A Generated Title"
    # Graph compilation builds each node's own model first; complete() builds
    # its own model on top, so the call it made is the last one recorded.
    provider, name, temperature, kwargs = factory.calls[-1]
    assert (provider, name, temperature) == ("fake", "fake-utility-model", 0.4)
    # Our per-call max_tokens wins over the config's own 999.
    assert kwargs == {"region": "us-east-1", "top_p": 0.8, "max_tokens": 24}


async def test_complete_sends_system_and_prompt_as_separate_messages() -> None:
    spec, base_dir = load_test_system()
    chat_model = RecordingChatModel()
    factory = RecordingModelFactory(chat_model)

    async with LangGraphEngine(base_dir, model_factory=factory) as engine:
        await engine.build(spec)
        await engine.complete("summarize this", system="be terse")

    messages, _config = chat_model.invocations[0]
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage]
    assert messages[0].content == "be terse"
    assert messages[1].content == "summarize this"


async def test_complete_without_a_system_prompt_sends_only_the_prompt() -> None:
    spec, base_dir = load_test_system()
    chat_model = RecordingChatModel()
    factory = RecordingModelFactory(chat_model)

    async with LangGraphEngine(base_dir, model_factory=factory) as engine:
        await engine.build(spec)
        await engine.complete("just this")

    messages, _config = chat_model.invocations[0]
    assert [type(m) for m in messages] == [HumanMessage]


async def test_complete_traces_under_the_given_name() -> None:
    spec, base_dir = load_test_system()
    chat_model = RecordingChatModel()
    factory = RecordingModelFactory(chat_model)

    async with LangGraphEngine(base_dir, model_factory=factory) as engine:
        await engine.build(spec)
        await engine.complete("hello", trace_name="conversation_title")

    _messages, config = chat_model.invocations[0]
    assert config["run_name"] == "conversation_title"
    assert config["tags"] == ["conversation_title"]
