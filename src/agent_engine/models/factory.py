"""Build a chat model from resolved model config.

This is the single chokepoint that translates provider + name + temperature
into a LangChain ``BaseChatModel``. The runtime depends only on
``BaseChatModel`` and this factory — never on a specific provider SDK.
Provider integration packages are imported lazily from this boundary.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agent_engine.logging_config import log

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("anthropic", "bedrock", "gemini")


class ModelConfigurationError(RuntimeError):
    """Raised when model provider config cannot produce a chat model."""


def build_chat_model(
    provider: str,
    name: str,
    temperature: float | None = None,
    *,
    region: str | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
) -> BaseChatModel:
    """Construct a chat model from flat config fields.

    Switching providers is a configuration change, not a code change.
    """
    normalized_provider = provider.strip().lower()
    model_name = name.strip()
    if not model_name:
        raise ModelConfigurationError("Model name must not be empty.")

    log(
        logger,
        logging.INFO,
        "llm configured",
        provider=normalized_provider,
        model=model_name,
        temperature=temperature,
        region=region,
    )
    if normalized_provider == "anthropic":
        return _build_anthropic_model(
            model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )
    if normalized_provider == "bedrock":
        return _build_bedrock_model(
            model_name,
            temperature=temperature,
            region=region,
            max_tokens=max_tokens,
            top_p=top_p,
        )
    if normalized_provider == "gemini":
        return _build_gemini_model(
            model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )
    supported = ", ".join(_SUPPORTED_PROVIDERS)
    raise ModelConfigurationError(
        f"Unsupported model provider '{provider}'. Supported providers: {supported}."
    )


def _build_anthropic_model(
    name: str,
    *,
    temperature: float | None,
    max_tokens: int | None,
    top_p: float | None,
) -> BaseChatModel:
    kwargs = _without_none(
        {
            "model_provider": "anthropic",
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
        }
    )
    return init_chat_model(name, **kwargs)


def _build_bedrock_model(
    name: str,
    *,
    temperature: float | None,
    region: str | None,
    max_tokens: int | None,
    top_p: float | None,
) -> BaseChatModel:
    resolved_region = _resolve_aws_region(region)
    if not resolved_region:
        raise ModelConfigurationError(
            "Bedrock model provider requires an AWS region. Set model.region in YAML, "
            "AWS_REGION, or AWS_DEFAULT_REGION."
        )

    if temperature is None:
        temperature_kwargs: dict[str, Any] = {}
    else:
        temperature_kwargs = {"temperature": temperature}

    try:
        from langchain_aws import ChatBedrockConverse
    except ImportError as exc:
        raise ModelConfigurationError(
            "Bedrock model provider requires the 'langchain-aws' package. "
            "Install the Bedrock extra/dependency before using provider: bedrock."
        ) from exc

    try:
        return ChatBedrockConverse(
            **_without_none(
                {
                    "model": name,
                    "region_name": resolved_region,
                    "max_tokens": max_tokens,
                    "top_p": top_p,
                    **temperature_kwargs,
                }
            )
        )
    except Exception as exc:
        raise ModelConfigurationError(
            "Could not initialize Bedrock chat model. Ensure AWS credentials and region "
            "are configured via the normal AWS credential chain."
        ) from exc


def _build_gemini_model(
    name: str,
    *,
    temperature: float | None,
    max_tokens: int | None,
    top_p: float | None,
) -> BaseChatModel:
    api_key = _resolve_gemini_api_key()
    if not api_key:
        raise ModelConfigurationError(
            "Gemini provider requires GEMINI_API_KEY. "
            "Set it in your environment before running agentctl."
        )

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise ModelConfigurationError(
            "Gemini model provider requires the 'langchain-google-genai' package. "
            "Install the Gemini extra/dependency before using provider: gemini "
            "(e.g. pip install 'agent-engine[gemini]')."
        ) from exc

    # Gemini names the output-token limit `max_output_tokens`; map extra's
    # provider-neutral `max_tokens` onto it. The API key is passed explicitly so
    # the same GEMINI_API_KEY works regardless of which env var the installed
    # SDK version reads by default.
    try:
        return ChatGoogleGenerativeAI(
            **_without_none(
                {
                    "model": name,
                    "google_api_key": api_key,
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                    "top_p": top_p,
                }
            )
        )
    except Exception as exc:
        raise ModelConfigurationError(
            "Could not initialize Gemini chat model. Verify GEMINI_API_KEY is valid "
            "and the model name is a Gemini model your key can access."
        ) from exc


def _resolve_aws_region(region: str | None) -> str | None:
    if region and region.strip():
        return region.strip()
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")


def _resolve_gemini_api_key() -> str | None:
    # GEMINI_API_KEY is the documented variable; GOOGLE_API_KEY is accepted as a
    # fallback because the underlying Google SDK reads it natively.
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    return key.strip() if key and key.strip() else None


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
