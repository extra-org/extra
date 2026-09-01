from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from agent_engine.logging_config import log
from agent_engine.observability.provider import CallbackProvider

logger = logging.getLogger("agent_engine.trace")


class LoggingCallbackHandler(BaseCallbackHandler):
    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        model = (serialized or {}).get("name") or (kwargs.get("invocation_params") or {}).get(
            "model"
        )
        log(logger, logging.INFO, "llm start", model=model, prompts=len(prompts))

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = None
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
        log(logger, logging.INFO, "llm end", tokens=usage)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        log(logger, logging.ERROR, "llm error", error=str(error))

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        log(logger, logging.INFO, "tool start", name=(serialized or {}).get("name", "?"))
        log(logger, logging.DEBUG, "tool input", chars=len(input_str))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        log(logger, logging.INFO, "tool end", status="ok")
        log(logger, logging.DEBUG, "tool output", output_type=type(output).__name__)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        log(logger, logging.WARNING, "tool end", status="error", error=str(error))

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any], **kwargs: Any
    ) -> None:
        name = (serialized or {}).get("name")
        if name:
            log(logger, logging.DEBUG, "chain start", name=name)


class LoggingProvider(CallbackProvider):
    name = "logging"

    def is_enabled(self) -> bool:
        return True

    def build(self) -> BaseCallbackHandler | None:
        return LoggingCallbackHandler()
