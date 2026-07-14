from __future__ import annotations

import logging
import sys
import types
from typing import Any, ClassVar

import pytest

from agent_engine.models import factory as factory_mod
from agent_engine.models.factory import ModelConfigurationError, build_chat_model


class _FakeAnthropicModel:
    pass


class _FakeBedrockModel:
    instances: ClassVar[list[_FakeBedrockModel]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.instances.append(self)


def _install_fake_langchain_aws(monkeypatch: pytest.MonkeyPatch, cls: type[Any]) -> None:
    module = types.ModuleType("langchain_aws")
    monkeypatch.setattr(module, "ChatBedrockConverse", cls, raising=False)
    monkeypatch.setitem(sys.modules, "langchain_aws", module)


class _FakeGeminiModel:
    instances: ClassVar[list[_FakeGeminiModel]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.instances.append(self)


def _install_fake_langchain_google_genai(
    monkeypatch: pytest.MonkeyPatch, cls: type[Any]
) -> None:
    module = types.ModuleType("langchain_google_genai")
    monkeypatch.setattr(module, "ChatGoogleGenerativeAI", cls, raising=False)
    monkeypatch.setitem(sys.modules, "langchain_google_genai", module)


class _FakeOpenAIModel:
    instances: ClassVar[list[_FakeOpenAIModel]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.instances.append(self)


def _install_fake_langchain_openai(monkeypatch: pytest.MonkeyPatch, cls: type[Any]) -> None:
    module = types.ModuleType("langchain_openai")
    monkeypatch.setattr(module, "ChatOpenAI", cls, raising=False)
    monkeypatch.setitem(sys.modules, "langchain_openai", module)


def test_anthropic_provider_still_uses_langchain_init_chat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    model = _FakeAnthropicModel()

    def fake_init_chat_model(name: str, **kwargs: Any) -> _FakeAnthropicModel:
        calls.append((name, kwargs))
        return model

    monkeypatch.setattr(factory_mod, "init_chat_model", fake_init_chat_model)

    result = build_chat_model("anthropic", "claude-haiku-4-5", temperature=0.0)

    assert result is model
    assert calls == [
        (
            "claude-haiku-4-5",
            {"model_provider": "anthropic", "temperature": 0.0},
        )
    ]


def test_bedrock_provider_creates_chat_bedrock_converse_with_yaml_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeBedrockModel.instances.clear()
    _install_fake_langchain_aws(monkeypatch, _FakeBedrockModel)

    result = build_chat_model(
        "bedrock",
        "anthropic.claude-3-5-haiku-20241022-v1:0",
        temperature=0.0,
        region="us-east-1",
        max_tokens=512,
        top_p=0.8,
    )

    assert result is _FakeBedrockModel.instances[0]
    assert _FakeBedrockModel.instances[0].kwargs == {
        "model": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "region_name": "us-east-1",
        "temperature": 0.0,
        "max_tokens": 512,
        "top_p": 0.8,
    }


def test_bedrock_region_can_come_from_aws_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeBedrockModel.instances.clear()
    _install_fake_langchain_aws(monkeypatch, _FakeBedrockModel)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    build_chat_model("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0")

    assert _FakeBedrockModel.instances[0].kwargs["region_name"] == "us-west-2"


def test_bedrock_region_can_come_from_aws_default_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeBedrockModel.instances.clear()
    _install_fake_langchain_aws(monkeypatch, _FakeBedrockModel)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")

    build_chat_model("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0")

    assert _FakeBedrockModel.instances[0].kwargs["region_name"] == "eu-central-1"


def test_bedrock_missing_region_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langchain_aws(monkeypatch, _FakeBedrockModel)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    with pytest.raises(ModelConfigurationError, match="requires an AWS region"):
        build_chat_model("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0")


def test_bedrock_construction_failure_mentions_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingBedrockModel:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("Unable to locate credentials")

    _install_fake_langchain_aws(monkeypatch, RaisingBedrockModel)

    with pytest.raises(ModelConfigurationError, match="AWS credentials"):
        build_chat_model(
            "bedrock",
            "anthropic.claude-3-5-haiku-20241022-v1:0",
            region="us-east-1",
        )


def test_gemini_provider_maps_config_onto_chat_google_generative_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeGeminiModel.instances.clear()
    _install_fake_langchain_google_genai(monkeypatch, _FakeGeminiModel)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    result = build_chat_model(
        "gemini",
        "gemini-2.5-flash",
        temperature=0.2,
        max_tokens=1024,
        top_p=0.9,
    )

    assert result is _FakeGeminiModel.instances[0]
    # `max_tokens` maps onto Gemini's `max_output_tokens`; region is not passed.
    assert _FakeGeminiModel.instances[0].kwargs == {
        "model": "gemini-2.5-flash",
        "google_api_key": "gm-test-key",
        "temperature": 0.2,
        "max_output_tokens": 1024,
        "top_p": 0.9,
    }


def test_gemini_missing_api_key_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langchain_google_genai(monkeypatch, _FakeGeminiModel)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(ModelConfigurationError, match="requires GEMINI_API_KEY"):
        build_chat_model("gemini", "gemini-2.5-flash")


def test_gemini_accepts_google_api_key_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeGeminiModel.instances.clear()
    _install_fake_langchain_google_genai(monkeypatch, _FakeGeminiModel)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-fallback-key")

    build_chat_model("gemini", "gemini-2.5-flash")

    assert _FakeGeminiModel.instances[0].kwargs["google_api_key"] == "google-fallback-key"


def test_gemini_missing_package_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    # Do not install the fake module: the real package is not a test dependency,
    # so the import must fail with an actionable install hint.
    monkeypatch.setitem(sys.modules, "langchain_google_genai", None)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-key")

    with pytest.raises(ModelConfigurationError, match="langchain-google-genai"):
        build_chat_model("gemini", "gemini-2.5-flash")


def test_gemini_construction_failure_does_not_leak_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingGeminiModel:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("invalid api key")

    _install_fake_langchain_google_genai(monkeypatch, RaisingGeminiModel)
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-gemini-key")

    with pytest.raises(ModelConfigurationError) as excinfo:
        build_chat_model("gemini", "gemini-2.5-flash")

    assert "super-secret-gemini-key" not in str(excinfo.value)


def test_gemini_does_not_log_api_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _FakeGeminiModel.instances.clear()
    _install_fake_langchain_google_genai(monkeypatch, _FakeGeminiModel)
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-gemini-key")

    with caplog.at_level(logging.INFO):
        build_chat_model("gemini", "gemini-2.5-flash", temperature=0.0)

    assert "super-secret-gemini-key" not in caplog.text


def test_openai_provider_maps_config_onto_chat_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeOpenAIModel.instances.clear()
    _install_fake_langchain_openai(monkeypatch, _FakeOpenAIModel)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test-key")

    result = build_chat_model(
        "openai",
        "gpt-4.1-mini",
        temperature=0.2,
        max_tokens=1024,
        top_p=0.9,
    )

    assert result is _FakeOpenAIModel.instances[0]
    # OpenAI keeps the `max_tokens` name; region is not passed.
    assert _FakeOpenAIModel.instances[0].kwargs == {
        "model": "gpt-4.1-mini",
        "api_key": "oa-test-key",
        "temperature": 0.2,
        "max_tokens": 1024,
        "top_p": 0.9,
    }


def test_openai_missing_api_key_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langchain_openai(monkeypatch, _FakeOpenAIModel)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ModelConfigurationError, match="requires OPENAI_API_KEY"):
        build_chat_model("openai", "gpt-4.1-mini")


def test_openai_missing_package_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    # Do not install the fake module: the real package is not a test dependency,
    # so the import must fail with an actionable install hint.
    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test-key")

    with pytest.raises(ModelConfigurationError, match="langchain-openai"):
        build_chat_model("openai", "gpt-4.1-mini")


def test_openai_construction_failure_does_not_leak_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingOpenAIModel:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("invalid api key")

    _install_fake_langchain_openai(monkeypatch, RaisingOpenAIModel)
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-openai-key")

    with pytest.raises(ModelConfigurationError) as excinfo:
        build_chat_model("openai", "gpt-4.1-mini")

    assert "super-secret-openai-key" not in str(excinfo.value)


def test_openai_does_not_log_api_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _FakeOpenAIModel.instances.clear()
    _install_fake_langchain_openai(monkeypatch, _FakeOpenAIModel)
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-openai-key")

    with caplog.at_level(logging.INFO):
        build_chat_model("openai", "gpt-4.1-mini", temperature=0.0)

    assert "super-secret-openai-key" not in caplog.text


def test_openai_base_url_points_at_a_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeOpenAIModel.instances.clear()
    _install_fake_langchain_openai(monkeypatch, _FakeOpenAIModel)
    monkeypatch.setenv("ZAI_API_KEY", "zai-test-key")

    result = build_chat_model(
        "openai",
        "glm-5.2",
        temperature=0.2,
        base_url="https://api.z.ai/api/coding/paas/v4",
        api_key_env="ZAI_API_KEY",
    )

    assert result is _FakeOpenAIModel.instances[0]
    assert _FakeOpenAIModel.instances[0].kwargs == {
        "model": "glm-5.2",
        "api_key": "zai-test-key",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "temperature": 0.2,
    }


def test_openai_api_key_env_defaults_to_openai_api_key_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeOpenAIModel.instances.clear()
    _install_fake_langchain_openai(monkeypatch, _FakeOpenAIModel)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test-key")

    build_chat_model("openai", "gpt-4.1-mini", api_key_env=None)

    assert _FakeOpenAIModel.instances[0].kwargs["api_key"] == "oa-test-key"


def test_openai_missing_custom_api_key_env_names_that_var_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_langchain_openai(monkeypatch, _FakeOpenAIModel)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    with pytest.raises(ModelConfigurationError, match="requires ZAI_API_KEY"):
        build_chat_model("openai", "glm-5.2", api_key_env="ZAI_API_KEY")


def test_unsupported_provider_is_rejected_clearly() -> None:
    with pytest.raises(ModelConfigurationError, match="Unsupported model provider 'cohere'"):
        build_chat_model("cohere", "command-r")


def test_model_factory_does_not_log_aws_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _FakeBedrockModel.instances.clear()
    _install_fake_langchain_aws(monkeypatch, _FakeBedrockModel)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST_SECRET")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret-value")

    with caplog.at_level(logging.INFO):
        build_chat_model(
            "bedrock",
            "anthropic.claude-3-5-haiku-20241022-v1:0",
            region="us-east-1",
        )

    assert "AKIA_TEST_SECRET" not in caplog.text
    assert "super-secret-value" not in caplog.text
