from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.callbacks import BaseCallbackHandler


class CallbackProvider(ABC):
    """One observability backend, expressed as a LangChain callback handler.

    Subclass, implement the two methods, register in ``registry.PROVIDERS``.
    """

    name: str

    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def build(self) -> BaseCallbackHandler | None:
        """Construct the handler, or None if it can't be built. Must not raise."""
