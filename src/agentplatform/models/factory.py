"""Build a chat model from a resolved ``ModelSpec`` (see ADR 0008).

This is the single chokepoint that translates the user-declared model
(``provider`` + ``name`` in YAML) into a LangChain ``BaseChatModel``. The
runtime depends only on ``BaseChatModel`` and this factory — never on a specific
provider SDK. Provider integration packages (``langchain-anthropic``,
``langchain-openai``, …) are optional and imported lazily by
``init_chat_model`` based on ``spec.provider``.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agentplatform.spec.models import ModelSpec


def build_chat_model(spec: ModelSpec) -> BaseChatModel:
    """Construct a chat model for one node's resolved model spec.

    The provider is chosen at runtime from ``spec.provider`` (a YAML value), so
    switching providers is a configuration change, not a code change.
    """
    if spec.temperature is None:
        return init_chat_model(spec.name, model_provider=spec.provider)
    return init_chat_model(
        spec.name, model_provider=spec.provider, temperature=spec.temperature
    )
