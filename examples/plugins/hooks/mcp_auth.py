"""Example MCP-auth runtime hook plugin.

This illustrates how a private application can authenticate calls to private
MCP servers without exposing credentials to the LLM and without adding
per-server client code to the platform core.

Registered in examples/plugins/plugins.toml as the ``mcp_auth`` hook plugin.
The agent YAML references this class by plugin id and method name.

SECURITY
--------
Hooks are trusted application code. Secrets come from environment variables
(or your secret manager) resolved here, never from YAML. Never log tokens,
signatures, Authorization headers, or inbound access tokens.

The hook instance is long-lived. It may safely keep config, initialized clients,
and keyed caches. It must not store unsafe per-request state such as the current
user, current organization, inbound token, request object, or "last request".
Per-request data comes from ``RunContext``/``AuthContext`` on each call.

Config keys avoid words like "token" or "secret" because the platform's YAML
secret scanner rejects those substrings even for env-var references.
"""

from __future__ import annotations

import os

from agent_engine.runtime.hooks import HookInvocation, McpRequestContext, RunContext, ToolCallContext


class McpAuthHook:
    """Class-based runtime hook with safe long-lived state.

    One instance is created when the HookManager loads this hook during engine
    build. The same instance is reused for later executions of that hook entry.
    """

    def __init__(self, config: object | None = None) -> None:
        self.config = dict(config or {})
        # Safe long-lived state: this records which env-var names were validated.
        # It never stores raw credential values or per-request identity.
        self._validated_credential_envs: set[str] = set()

    def validate_auth_setup(self, event: HookInvocation) -> None:
        """on_engine_start: fail fast if required env vars are absent."""
        config = dict(event.config or {})
        required = config.get("required_env", [])
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        self._validated_credential_envs.update(str(name) for name in required)

    def attach_user_context(self, event: HookInvocation) -> RunContext:
        """on_run_start: enrich the run with safe correlation metadata."""
        context = event.payload_as(RunContext)
        config = dict(event.config or {})
        return context.replace(
            metadata={**context.metadata, "app": config.get("app_name", "enterprise-app")}
        )

    async def before_mcp_request(self, event: HookInvocation) -> McpRequestContext:
        """before_mcp_request: add auth headers without storing request state."""
        request = event.payload_as(McpRequestContext)
        context = event.run_context
        config = dict(event.config or {})
        credential_env = str(config["credential_env"])
        credential = os.environ[credential_env]
        headers = {"Authorization": f"Bearer {credential}"}

        if context and context.organization_id:
            headers["X-Org-Id"] = context.organization_id
        if context and context.run_id:
            headers["X-Run-Id"] = context.run_id

        return request.with_headers(headers)

    def record_tool_call(self, event: HookInvocation) -> None:
        """after_tool_call: best-effort audit with safe metadata only."""
        context = event.run_context
        call = event.payload_as(ToolCallContext)
        run_id = context.run_id if context else None
        print(  # noqa: T201 - example only; use your audit sink in production
            f"[audit] run={run_id} agent={call.agent_id} tool={call.tool_name} "
            f"provider={call.provider} server={call.server_id} status={call.status} "
            f"latency_ms={call.latency_ms}"
        )

    def record_run_failure(self, event: HookInvocation) -> None:
        """on_run_error: audit the failure without logging sensitive details."""
        context = event.run_context
        error = event.payload_as(BaseException)
        run_id = context.run_id if context else None
        print(f"[audit] run={run_id} FAILED: {type(error).__name__}")  # noqa: T201
