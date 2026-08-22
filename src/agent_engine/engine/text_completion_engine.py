"""Optional engine capability for a single stateless text completion.

For side-work that wants a model's answer without the graph: no compiled
nodes, no tools, no protected-node filtering, no prompt-template rendering.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextCompletionEngine(Protocol):
    @property
    def can_complete_text(self) -> bool:
        """Whether the system has a model configured for `complete`.

        Ask before calling: a system with no default model answers `False`
        rather than `complete` raising, so a caller can degrade without
        needing to catch an engine-configuration error alongside real
        completion failures (a bad response, a provider outage).
        """
        ...

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        trace_name: str | None = None,
    ) -> str: ...
