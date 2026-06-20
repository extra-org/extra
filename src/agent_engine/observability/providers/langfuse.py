from __future__ import annotations

import logging
import os

from langchain_core.callbacks import BaseCallbackHandler

from agent_engine.observability.provider import CallbackProvider

logger = logging.getLogger(__name__)


class LangfuseProvider(CallbackProvider):
    """Langfuse — full trace of prompts, responses, and routing decisions.

    The handler reads host URL and other config from its own env vars.
    """

    name = "langfuse"

    def is_enabled(self) -> bool:
        return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))

    def build(self) -> BaseCallbackHandler | None:
        try:
            try:
                from langfuse.callback import CallbackHandler  # v2
            except ImportError:
                from langfuse.langchain import CallbackHandler  # v3+
            return CallbackHandler()
        except Exception as exc:
            logger.warning("langfuse: handler could not be built: %s", exc)
            return None
