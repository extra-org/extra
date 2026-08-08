from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.messages.tool import ToolCall

from agent_engine.core.spec import SystemSpec
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.types import RunResult


class FakeRunnableWithFallbacks:
    def __init__(self, primary: Any, fallbacks: list[Any]) -> None:
        self._primary = primary
        self._fallbacks = fallbacks

    async def ainvoke(self, messages: list[Any]) -> Any:
        try:
            return await self._primary.ainvoke(messages)
        except Exception:
            for fallback in self._fallbacks:
                try:
                    return await fallback.ainvoke(messages)
                except Exception:
                    continue
            raise

    async def astream(self, messages: list[Any]) -> Any:
        try:
            async for chunk in self._primary.astream(messages):
                yield chunk
        except Exception:
            for fallback in self._fallbacks:
                try:
                    async for chunk in fallback.astream(messages):
                        yield chunk
                    return
                except Exception:
                    continue
            raise


class FailingChatModel:
    def bind_tools(self, tools: list[Any]) -> FailingChatModel:
        return self

    def with_fallbacks(
        self,
        fallbacks: list[Any],
        exceptions_to_handle: tuple[type[BaseException], ...] = (Exception,),
    ) -> Any:
        return FakeRunnableWithFallbacks(self, fallbacks)

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        raise RuntimeError("Model execution failed")

    async def astream(self, messages: list[Any]) -> Any:
        if False:
            yield AIMessage(content="")
        raise RuntimeError("Model stream failed")


class FakeChatModel:
    def __init__(
        self,
        answer: str = "ok",
        tool_names: list[str] | None = None,
    ) -> None:
        self._answer = answer
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> FakeChatModel:
        return FakeChatModel(
            self._answer,
            [t.name for t in tools],
        )

    def with_fallbacks(
        self,
        fallbacks: list[Any],
        exceptions_to_handle: tuple[type[BaseException], ...] = (Exception,),
    ) -> Any:
        return FakeRunnableWithFallbacks(self, fallbacks)

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> Any:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        already_called = any(m.__class__.__name__ == "ToolMessage" for m in messages)
        if self._tool_names and not already_called:
            return AIMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        name=self._select(messages),
                        args={"message": self._user_text(messages)},
                        id="call_1",
                    )
                ],
            )
        return AIMessage(content=self._answer)

    def _select(self, messages: list[Any]) -> str:
        text = self._user_text(messages).lower()
        for name in self._tool_names:
            if name.lower() in text:
                return name
        return self._tool_names[0]

    @staticmethod
    def _user_text(messages: list[Any]) -> str:
        for m in reversed(messages):
            if m.__class__.__name__ == "HumanMessage":
                return str(m.content)
        return ""


def fake_model_factory(
    provider: str = "fake",
    name: str = "fake",
    temperature: float | None = None,
    *,
    answer: str = "ok",
) -> Any:
    if name == "failing-primary":
        return FailingChatModel()
    if name == "successful-fallback":
        return FakeChatModel(answer="recovered ok")
    return FakeChatModel(answer=answer)


FIXTURES_ROOT = Path(__file__).resolve().parent


def fixture_path(*parts: str) -> Path:
    return FIXTURES_ROOT.joinpath(*parts)


def load_test_system(spec_path: Path | None = None) -> tuple[SystemSpec, Path]:
    if spec_path is None:
        spec_path = FIXTURES_ROOT / "test_system" / "agents.yaml"
    base_dir = spec_path.resolve().parent
    from agent_engine.core.validator import SystemSpecValidator
    from agent_engine.parsers.yaml.parser import YAMLParser

    spec = YAMLParser().parse(str(spec_path))
    errors = SystemSpecValidator().validate(spec, base_dir)
    if errors:
        raise AssertionError("Fixture validation failed:\n" + "\n".join(str(e) for e in errors))
    return spec, base_dir


class FakeEngine:
    def __init__(self, answer: str = "ok") -> None:
        self._answer = answer
        self._engine: LangGraphEngine | None = None

    async def __aenter__(self) -> FakeEngine:
        spec, base_dir = load_test_system()
        self._engine = LangGraphEngine(base_dir, model_factory=fake_model_factory)
        await self._engine.build(spec)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._engine is not None:
            await self._engine.__aexit__(*args)

    async def run(self, message: str) -> RunResult:
        assert self._engine is not None
        return await self._engine.run(message)
