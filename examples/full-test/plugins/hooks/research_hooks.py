from __future__ import annotations

from agent_engine.runtime.hooks import HookInvocation


class ResearchHooksHook:
    def __init__(self, config: object | None = None) -> None:
        self.config = config
        # Safe long-lived state can live here: initialized clients,
        # tenant metadata, keyed caches, audit/metrics clients.
        self._cache: dict[str, object] = {}
        # Do not store per-request state such as current user, current
        # organization, inbound tokens, request objects, or last headers.
        # Read that data from event.run_context and event.payload.

    async def validate_environment(self, event: HookInvocation) -> object:
        raise NotImplementedError

    async def inject_context7_auth(self, event: HookInvocation) -> object:
        raise NotImplementedError

    async def audit_tool_call(self, event: HookInvocation) -> object:
        raise NotImplementedError

    async def record_run_failure(self, event: HookInvocation) -> object:
        raise NotImplementedError
