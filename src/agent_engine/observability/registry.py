from __future__ import annotations

import logging

from langchain_core.callbacks import BaseCallbackHandler

from agent_engine.observability.provider import CallbackProvider
from agent_engine.observability.providers.langfuse import LangfuseProvider

logger = logging.getLogger(__name__)

# Add a backend: write a CallbackProvider, append it here.
PROVIDERS: list[CallbackProvider] = [
    LangfuseProvider(),
]


def build_callbacks() -> list[BaseCallbackHandler]:
    """Handlers for every enabled provider. Host calls this; injects into engine."""
    callbacks: list[BaseCallbackHandler] = []
    for provider in PROVIDERS:
        if not provider.is_enabled():
            continue
        handler = provider.build()
        if handler is None:
            continue
        logger.info("observability: %s enabled", provider.name)
        callbacks.append(handler)
    return callbacks
