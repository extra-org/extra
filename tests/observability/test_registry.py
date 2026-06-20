from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler

from agent_engine.observability import registry
from agent_engine.observability.provider import CallbackProvider


class _Handler(BaseCallbackHandler):
    pass


class _FakeProvider(CallbackProvider):
    def __init__(self, name: str, *, enabled: bool, handler: BaseCallbackHandler | None):
        self.name = name
        self._enabled = enabled
        self._handler = handler

    def is_enabled(self) -> bool:
        return self._enabled

    def build(self) -> BaseCallbackHandler | None:
        return self._handler


def _run(providers: list[CallbackProvider], monkeypatch) -> list[BaseCallbackHandler]:
    monkeypatch.setattr(registry, "PROVIDERS", providers)
    return registry.build_callbacks()


def test_enabled_provider_is_included(monkeypatch):
    handler = _Handler()
    result = _run([_FakeProvider("a", enabled=True, handler=handler)], monkeypatch)
    assert result == [handler]


def test_disabled_provider_is_skipped(monkeypatch):
    result = _run([_FakeProvider("a", enabled=False, handler=_Handler())], monkeypatch)
    assert result == []


def test_build_returning_none_is_skipped(monkeypatch):
    result = _run([_FakeProvider("a", enabled=True, handler=None)], monkeypatch)
    assert result == []


def test_only_enabled_providers_collected(monkeypatch):
    h1, h2 = _Handler(), _Handler()
    result = _run(
        [
            _FakeProvider("on1", enabled=True, handler=h1),
            _FakeProvider("off", enabled=False, handler=_Handler()),
            _FakeProvider("on2", enabled=True, handler=h2),
        ],
        monkeypatch,
    )
    assert result == [h1, h2]


def test_langfuse_provider_disabled_without_keys(monkeypatch):
    from agent_engine.observability.providers.langfuse import LangfuseProvider

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert LangfuseProvider().is_enabled() is False


def test_langfuse_provider_enabled_with_keys(monkeypatch):
    from agent_engine.observability.providers.langfuse import LangfuseProvider

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert LangfuseProvider().is_enabled() is True
