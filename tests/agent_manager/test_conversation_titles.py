"""Conversation titling — pure: in-memory repository, stub engine, stub model."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from agent_engine.core.spec import BaseModelConfig
from agent_engine.engine.types import ChatMessage, RunResult
from agent_engine.runtime.hooks.models import RunContext
from agent_manager.application import ConversationService
from agent_manager.domain import Principal, TitleGenerator
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.titles import (
    MAX_OUTPUT_TOKENS,
    MAX_SOURCE_CHARS,
    MAX_TITLE_CHARS,
    TRACE_NAME,
    ConversationTitler,
    build_titler,
    parse_model_ref,
)
from tests.agent_manager.conftest import RecordingEngine

ALICE = Principal.external("alice")
LONG_QUESTION = (
    "Can you estimate my invoice for next month based on the last eight months of usage?"
)


TURN_INPUT_TOKENS = 100
TURN_OUTPUT_TOKENS = 20


class MeteredEngine(RecordingEngine):
    """A stub Engine that reports what one turn cost, so budgets are assertable."""

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        result = await super().run(message, history=history, context=context)
        return dataclasses.replace(
            result, input_tokens=TURN_INPUT_TOKENS, output_tokens=TURN_OUTPUT_TOKENS
        )


class BrokenTitleGenerator(TitleGenerator):
    """A generator that breaks its own contract by raising."""

    async def generate(self, text: str, conversation_id: str) -> str:
        raise RuntimeError("generator unavailable")


class StubChatModel:
    """Records what it was asked and answers with a canned title."""

    def __init__(self, answer: str | BaseMessage = "Next Month Invoice Estimate") -> None:
        self.answer = AIMessage(content=answer) if isinstance(answer, str) else answer
        self.calls: list[tuple[list[BaseMessage], dict[str, Any]]] = []

    async def ainvoke(
        self, messages: list[BaseMessage], config: dict[str, Any] | None = None, **_: Any
    ) -> BaseMessage:
        self.calls.append((messages, dict(config or {})))
        return self.answer


class HangingChatModel(StubChatModel):
    async def ainvoke(
        self, messages: list[BaseMessage], config: dict[str, Any] | None = None, **_: Any
    ) -> BaseMessage:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FailingChatModel(StubChatModel):
    async def ainvoke(
        self, messages: list[BaseMessage], config: dict[str, Any] | None = None, **_: Any
    ) -> BaseMessage:
        raise RuntimeError("provider unavailable")


def titler(model: StubChatModel, **kwargs: Any) -> ConversationTitler:
    return ConversationTitler(cast(BaseChatModel, model), **kwargs)


async def title_of(model: StubChatModel, text: str, **kwargs: Any) -> str:
    return await titler(model, **kwargs).generate(text, "c-1")


async def test_titles_a_long_opening_message_with_the_model() -> None:
    model = StubChatModel()

    assert await title_of(model, LONG_QUESTION) == "Next Month Invoice Estimate"


async def test_short_opening_message_is_its_own_title_without_a_model_call() -> None:
    model = StubChatModel()

    assert await title_of(model, "  Reset my\n password  ") == "Reset my password"
    assert model.calls == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ('"Next Month Invoice Estimate"', "Next Month Invoice Estimate"),
        ("'Next Month Invoice Estimate'", "Next Month Invoice Estimate"),
        ("“Next Month Invoice Estimate”", "Next Month Invoice Estimate"),
        ("«Next Month Invoice Estimate»", "Next Month Invoice Estimate"),
        ("「Next Month Invoice Estimate」", "Next Month Invoice Estimate"),
        ("״הערכת חשבונית לחודש הבא״", "הערכת חשבונית לחודש הבא"),
        ("Next Month Invoice Estimate.", "Next Month Invoice Estimate"),
        ("Title\nplus commentary the model added", "Title"),
        ("x" * 200, "x" * MAX_TITLE_CHARS),
    ],
)
async def test_the_model_answer_is_reduced_to_a_label(answer: str, expected: str) -> None:
    assert await title_of(StubChatModel(answer), LONG_QUESTION) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "   ",
        '""',
        "\n\n\n",
        '"',
        "«»",
        "”",  # a lone closing mark with no matching opener
        "😀🎉",
    ],
)
async def test_an_unusable_answer_falls_back_to_the_trim(answer: str) -> None:
    assert await title_of(StubChatModel(answer), LONG_QUESTION) == LONG_QUESTION[:47] + "…"


async def test_a_failing_provider_falls_back_to_the_trim() -> None:
    assert await title_of(FailingChatModel(), LONG_QUESTION) == LONG_QUESTION[:47] + "…"


async def test_a_hanging_provider_falls_back_to_the_trim() -> None:
    title = await title_of(HangingChatModel(), LONG_QUESTION, timeout_seconds=0.01)

    assert title == LONG_QUESTION[:47] + "…"


@pytest.mark.parametrize(
    "answer",
    [
        "\x00\x01",
        "'" * 200,
        "”«»„",
        "the\rtitle",
        "🎉" * 100,
    ],
)
async def test_no_model_answer_ever_raises_or_produces_an_empty_title(answer: str) -> None:
    """Whatever text a model returns, titling degrades — it never crashes the turn."""
    title = await title_of(StubChatModel(answer), LONG_QUESTION)

    assert title


async def test_the_opening_message_is_bounded_before_it_reaches_the_model() -> None:
    model = StubChatModel()

    await title_of(model, "word " * 1000)

    (_system, human), _config = model.calls[0]
    assert len(str(human.content)) == MAX_SOURCE_CHARS


async def test_the_call_is_traced_under_its_own_name() -> None:
    model = StubChatModel()

    await title_of(model, LONG_QUESTION)

    _messages, config = model.calls[0]
    assert config["run_name"] == TRACE_NAME
    assert config["metadata"] == {"conversation_id": "c-1"}


async def test_generated_title_replaces_the_trim_on_the_first_turn() -> None:
    repository = MemoryRepository()
    service = ConversationService(
        RecordingEngine(), repository, title_generator=titler(StubChatModel())
    )
    cid = await service.create(ALICE)

    await service.send(cid, LONG_QUESTION, ALICE)
    await service.close()

    session = await repository.get_session(cid)
    assert session is not None
    assert session.title == "Next Month Invoice Estimate"


async def test_titling_never_touches_the_conversation_or_its_token_budget() -> None:
    """The generated title costs tokens; the caller must not pay for them.

    Fails the moment titling starts persisting messages — which would both spend
    the conversation's budget and replay the title prompt as history.
    """
    repository = MemoryRepository()
    service = ConversationService(
        MeteredEngine(), repository, title_generator=titler(StubChatModel())
    )
    cid = await service.create(ALICE)

    await service.send(cid, LONG_QUESTION, ALICE)
    await service.close()

    messages = await repository.list_conversation_messages(cid)
    assert [message.content for message in messages] == [
        LONG_QUESTION,
        f"answer:{LONG_QUESTION}",
    ]
    assert await repository.get_token_usage(cid) == TURN_INPUT_TOKENS + TURN_OUTPUT_TOKENS


async def test_only_the_first_turn_is_titled() -> None:
    model = StubChatModel()
    service = ConversationService(
        RecordingEngine(), MemoryRepository(), title_generator=titler(model)
    )
    cid = await service.create(ALICE)

    await service.send(cid, LONG_QUESTION, ALICE)
    await service.send(cid, f"and {LONG_QUESTION}", ALICE)
    await service.close()

    assert len(model.calls) == 1


async def test_a_failing_generator_leaves_the_trimmed_title_in_place() -> None:
    repository = MemoryRepository()
    service = ConversationService(
        RecordingEngine(), repository, title_generator=BrokenTitleGenerator()
    )
    cid = await service.create(ALICE)

    result = await service.send(cid, LONG_QUESTION, ALICE)
    await service.close()

    assert result.answer == f"answer:{LONG_QUESTION}"
    session = await repository.get_session(cid)
    assert session is not None
    assert session.title == LONG_QUESTION[:47] + "…"


async def test_without_a_generator_the_trim_is_the_title() -> None:
    repository = MemoryRepository()
    service = ConversationService(RecordingEngine(), repository)
    cid = await service.create(ALICE)

    await service.send(cid, LONG_QUESTION, ALICE)

    session = await repository.get_session(cid)
    assert session is not None
    assert session.title == LONG_QUESTION[:47] + "…"


def test_a_deployment_without_a_model_keeps_trimming() -> None:
    assert build_titler(model_ref=None, default_model=None) is None


class RecordingModelFactory:
    """Stands in for `build_chat_model`: returns a stub, remembers what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, provider: str, name: str, **kwargs: Any) -> StubChatModel:
        self.calls.append((provider, name, kwargs))
        return StubChatModel()


def test_falls_back_to_the_systems_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = RecordingModelFactory()
    monkeypatch.setattr("agent_manager.infrastructure.titles.build_chat_model", factory)

    titler = build_titler(
        model_ref=None, default_model=BaseModelConfig(provider="anthropic", name="system-model")
    )

    assert isinstance(titler, ConversationTitler)
    assert factory.calls == [("anthropic", "system-model", {"max_tokens": MAX_OUTPUT_TOKENS})]


def test_an_explicit_model_ref_overrides_the_systems_own_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = RecordingModelFactory()
    monkeypatch.setattr("agent_manager.infrastructure.titles.build_chat_model", factory)

    build_titler(
        model_ref="anthropic:claude-haiku-4-5",
        default_model=BaseModelConfig(provider="anthropic", name="system-model"),
    )

    assert [(provider, name) for provider, name, _ in factory.calls] == [
        ("anthropic", "claude-haiku-4-5")
    ]


@pytest.mark.parametrize("ref", ["claude-haiku-4-5", "anthropic:", ":name", "", "  :  "])
def test_a_malformed_model_reference_is_rejected(ref: str) -> None:
    with pytest.raises(ValueError, match="provider:name"):
        parse_model_ref(ref)


def test_a_model_reference_names_provider_and_model() -> None:
    config = parse_model_ref(" anthropic : claude-haiku-4-5 ")

    assert (config.provider, config.name) == ("anthropic", "claude-haiku-4-5")
