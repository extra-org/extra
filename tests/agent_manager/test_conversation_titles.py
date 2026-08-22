"""Conversation titling — pure: in-memory repository, stub engine, stub completer."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import Any

import pytest

from agent_engine.core.spec import BaseModelConfig
from agent_engine.engine.types import ChatMessage, RunResult
from agent_engine.runtime.hooks.models import RunContext
from agent_manager.api.app import _title_generator
from agent_manager.application import ConversationService
from agent_manager.domain import Principal, TitleGenerator
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.titles import (
    MAX_OUTPUT_TOKENS,
    MAX_SOURCE_CHARS,
    MAX_TITLE_CHARS,
    TRACE_NAME,
    ConversationTitler,
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


class StubCompletionEngine:
    """A minimal TextCompletionEngine: records what it was asked, answers with a canned title."""

    def __init__(self, answer: str = "Next Month Invoice Estimate") -> None:
        self.answer = answer
        self.can_complete_text = True
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        *,
        model: BaseModelConfig | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        trace_name: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "system": system,
                "max_tokens": max_tokens,
                "trace_name": trace_name,
            }
        )
        return self.answer


class HangingCompletionEngine(StubCompletionEngine):
    async def complete(self, prompt: str, **kwargs: Any) -> str:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FailingCompletionEngine(StubCompletionEngine):
    async def complete(self, prompt: str, **kwargs: Any) -> str:
        raise RuntimeError("provider unavailable")


def titler(engine: StubCompletionEngine, **kwargs: Any) -> ConversationTitler:
    return ConversationTitler(engine, **kwargs)


async def title_of(engine: StubCompletionEngine, text: str, **kwargs: Any) -> str:
    return await titler(engine, **kwargs).generate(text, "c-1")


async def test_titles_a_long_opening_message_with_the_model() -> None:
    engine = StubCompletionEngine()

    assert await title_of(engine, LONG_QUESTION) == "Next Month Invoice Estimate"


async def test_short_opening_message_is_its_own_title_without_a_model_call() -> None:
    engine = StubCompletionEngine()

    assert await title_of(engine, "  Reset my\n password  ") == "Reset my password"
    assert engine.calls == []


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
    assert await title_of(StubCompletionEngine(answer), LONG_QUESTION) == expected


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
    assert await title_of(StubCompletionEngine(answer), LONG_QUESTION) == LONG_QUESTION[:47] + "…"


async def test_a_failing_provider_falls_back_to_the_trim() -> None:
    assert await title_of(FailingCompletionEngine(), LONG_QUESTION) == LONG_QUESTION[:47] + "…"


async def test_a_hanging_provider_falls_back_to_the_trim() -> None:
    title = await title_of(HangingCompletionEngine(), LONG_QUESTION, timeout_seconds=0.01)

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
    title = await title_of(StubCompletionEngine(answer), LONG_QUESTION)

    assert title


async def test_the_opening_message_is_bounded_before_it_reaches_the_model() -> None:
    engine = StubCompletionEngine()

    await title_of(engine, "word " * 1000)

    assert len(engine.calls[0]["prompt"]) == MAX_SOURCE_CHARS


async def test_the_call_is_traced_with_its_own_name_and_output_cap() -> None:
    engine = StubCompletionEngine()

    await title_of(engine, LONG_QUESTION)

    call = engine.calls[0]
    assert call["trace_name"] == TRACE_NAME
    assert call["max_tokens"] == MAX_OUTPUT_TOKENS


async def test_generated_title_replaces_the_trim_on_the_first_turn() -> None:
    repository = MemoryRepository()
    service = ConversationService(
        RecordingEngine(), repository, title_generator=titler(StubCompletionEngine())
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
        MeteredEngine(), repository, title_generator=titler(StubCompletionEngine())
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
    engine = StubCompletionEngine()
    service = ConversationService(
        RecordingEngine(), MemoryRepository(), title_generator=titler(engine)
    )
    cid = await service.create(ALICE)

    await service.send(cid, LONG_QUESTION, ALICE)
    await service.send(cid, f"and {LONG_QUESTION}", ALICE)
    await service.close()

    assert len(engine.calls) == 1


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


class FakeEngineWithCompletion:
    """Structurally satisfies TextCompletionEngine, whether or not it's usable."""

    def __init__(self, *, can_complete_text: bool) -> None:
        self.can_complete_text = can_complete_text

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        return "ok"


class PlainEngine:
    """Has neither `can_complete_text` nor `complete` — no completion capability."""


def test_title_generator_uses_the_engine_when_it_can_complete_text() -> None:
    generator = _title_generator(FakeEngineWithCompletion(can_complete_text=True))

    assert isinstance(generator, ConversationTitler)


def test_title_generator_is_none_when_the_engine_has_no_default_model() -> None:
    assert _title_generator(FakeEngineWithCompletion(can_complete_text=False)) is None


def test_title_generator_is_none_for_an_engine_without_the_capability() -> None:
    assert _title_generator(PlainEngine()) is None
