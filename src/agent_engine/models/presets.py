"""Known OpenAI-compatible vendor presets.

Each preset supplies the ``base_url`` and ``api_key_env`` for a vendor whose
API speaks the OpenAI chat completions protocol, so ``provider: <preset id>``
in YAML resolves those two fields automatically instead of requiring them to
be typed out by hand every time. A YAML ``model`` block can still override
``base_url`` and/or ``api_key_env`` for a listed vendor (a self-hosted proxy
in front of it, a non-default key variable name, ...), and ``provider:
openai`` with explicit ``base_url``/``api_key_env`` remains the escape hatch
for any vendor or self-hosted server not listed here.

Presets deliberately do not carry a default model id: no provider in this
factory picks a model on the caller's behalf, and vendor model catalogs
change independently of this codebase, so ``name`` stays required in YAML
exactly as it is for every other provider.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAICompatPreset:
    id: str
    name: str
    base_url: str
    api_key_env: str


_PRESET_LIST: tuple[OpenAICompatPreset, ...] = (
    OpenAICompatPreset(
        id="zai",
        name="Z.AI",
        base_url="https://api.z.ai/api/coding/paas/v4",
        api_key_env="ZAI_API_KEY",
    ),
    OpenAICompatPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    OpenAICompatPreset(
        id="moonshot",
        name="Moonshot",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
    ),
    OpenAICompatPreset(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
    ),
    OpenAICompatPreset(
        id="xai",
        name="xAI",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
    ),
    OpenAICompatPreset(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    ),
)

OPENAI_COMPAT_PRESETS: dict[str, OpenAICompatPreset] = {p.id: p for p in _PRESET_LIST}
