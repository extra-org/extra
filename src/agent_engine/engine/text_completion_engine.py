"""Optional engine capability for a single stateless text completion.

For side-work that wants a model's answer without the graph: no compiled
nodes, no tools, no protected-node filtering, no prompt-template rendering.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_engine.core.spec import BaseModelConfig


@runtime_checkable
class TextCompletionEngine(Protocol):
    @property
    def can_complete_text(self) -> bool:
        """Whether the system has a model to complete with when `model` is omitted.

        Ask before calling with no `model`: a system with no default model
        answers `False` rather than `complete` raising, so a caller can
        degrade without needing to catch an engine-configuration error
        alongside real completion failures (a bad response, a provider
        outage). Irrelevant to a call that supplies its own `model`.
        """
        ...

    async def complete(
        self,
        prompt: str,
        *,
        model: BaseModelConfig | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        trace_name: str | None = None,
    ) -> str:
        """Complete `prompt`, optionally preceded by a `system` instruction.

        `model` picks the model for this call; omitted, the engine's own
        default is used (see `can_complete_text`). `max_tokens` bounds this
        call's output regardless of `model`'s own configured size.
        `trace_name` labels the call in trace tooling.
        """
        ...
