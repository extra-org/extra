"""Runtime hook plugin for the Enterprise Knowledge Assistant example.

Demonstrates the framework's four most useful hook lifecycle points without ever
writing secrets into YAML or leaking credentials into logs or the LLM:

* ``on_engine_start``    → ``validate_environment``: fail fast at build time if a
  required credential is missing.
* ``before_mcp_request`` → ``inject_context7_auth``: attach Context7's auth header
  — and **only** for the ``context7`` server. DeepWiki is public and stays
  unauthenticated.
* ``after_tool_call``    → ``audit_tool_call``: best-effort audit of *safe* tool
  metadata (declared ``failure_policy: warn`` in YAML).
* ``transform_tool_result`` → ``truncate_tool_result``: cap oversized DeepWiki
  results before they enter the agent conversation.
* ``on_run_error``       → ``record_run_failure``: record a run failure by type.

SECURITY
--------
Hooks are trusted application code. Credentials are read from environment
variables here, never from YAML (the YAML loader also rejects secret-like
values). The audit log records only safe metadata — never API keys, headers,
prompts, tool arguments/results, or user data.
"""

from __future__ import annotations

import logging
import os

from agent_engine.runtime.hooks import (
    HookInvocation,
    McpRequestContext,
    ToolCallContext,
    ToolResultContext,
)

logger = logging.getLogger("research_hooks")

# Cap large DeepWiki MCP results before they are appended to the agent
# conversation, to keep prompts from blowing up.
_DEEPWIKI_SERVER = "deepwiki"
_MAX_MCP_RESULT_CHARS = 8000

# Environment variable NAMES the system needs. These are names, not secrets;
# the values are read from os.environ only at runtime and never logged.
_REQUIRED_ENV: tuple[str, ...] = ("ANTHROPIC_API_KEY", "CONTEXT7_API_KEY")

# The single authenticated MCP server, its credential env var, and Context7's
# documented header name. DeepWiki is public and intentionally absent here.
_CONTEXT7_SERVER = "context7"
_CONTEXT7_KEY_ENV = "CONTEXT7_API_KEY"
_CONTEXT7_HEADER = "CONTEXT7_API_KEY"  # Context7 reads the key from this header


class ResearchHooksHook:
    def __init__(self) -> None:
        # Safe long-lived state can live here: initialized clients,
        # tenant metadata, keyed caches, audit/metrics clients.
        self._cache: dict[str, object] = {}
        # Do not store per-request state such as current user, current
        # organization, inbound tokens, request objects, or last headers.
        # Read that data from event.run_context and event.payload.

    # -- on_engine_start ----------------------------------------------------
    async def validate_environment(self, event: HookInvocation) -> None:
        """Fail fast at build time if any required credential is absent.

        Raising here (default fail-closed policy) aborts ``Engine.build`` before a
        single request is served, so misconfiguration surfaces immediately.
        """
        missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    # -- before_mcp_request -------------------------------------------------
    async def inject_context7_auth(self, event: HookInvocation) -> McpRequestContext:
        """Attach Context7's auth header — only for the context7 server.

        The hook runs for every MCP server, so we gate on ``server_id``: DeepWiki
        (public) passes through untouched; context7 gets its key from the
        environment, never from YAML.
        """
        request = event.payload_as(McpRequestContext)
        if request.server_id != _CONTEXT7_SERVER:
            return request  # e.g. deepwiki — leave unauthenticated
        api_key = os.environ.get(_CONTEXT7_KEY_ENV)
        if not api_key:
            # on_engine_start already validated this; stay safe if it changed.
            return request
        return request.with_headers({_CONTEXT7_HEADER: api_key})

    # -- after_tool_call (failure_policy: warn) -----------------------------
    async def audit_tool_call(self, event: HookInvocation) -> None:
        """Best-effort audit of safe tool-call metadata only.

        Logs identifiers and timing — never API keys, headers, tool arguments,
        tool results, prompts, or user data.
        """
        call = event.payload_as(ToolCallContext)
        run_id = event.run_context.run_id if event.run_context else None
        logger.info(
            "tool_call audit run_id=%s node=%s tool=%s provider=%s server=%s "
            "status=%s elapsed_ms=%s",
            run_id,
            call.agent_id,
            call.tool_name,
            call.provider,
            call.server_id,
            call.status,
            call.latency_ms,
        )

    # -- transform_tool_result (failure_policy: warn) -----------------------
    async def truncate_tool_result(self, event: HookInvocation) -> ToolResultContext:
        """Cap oversized MCP results before they reach the agent conversation.

        DeepWiki pages can be very large and blow up the prompt. We truncate
        oversized results from the ``deepwiki`` MCP server and log only **safe
        metadata** (sizes, names, ids) — never the result content. Other MCP
        servers, local tools, and small results pass through unchanged.
        Returning the (possibly modified) context is what the engine appends to
        the conversation.
        """
        result = event.payload_as(ToolResultContext)
        text = result.result
        if (
            result.provider != "mcp"
            or result.server_id != _DEEPWIKI_SERVER
            or len(text) <= _MAX_MCP_RESULT_CHARS
        ):
            return result  # non-DeepWiki tools / small DeepWiki results: unchanged
        run_id = event.run_context.run_id if event.run_context else None
        logger.info(
            "tool_result truncated run_id=%s tool=%s server=%s original_chars=%d kept_chars=%d",
            run_id,
            result.tool_name,
            result.server_id,
            len(text),
            _MAX_MCP_RESULT_CHARS,
        )
        notice = (
            f"\n\n[truncated to {_MAX_MCP_RESULT_CHARS} of {len(text)} chars "
            f"by the transform_tool_result hook]"
        )
        return result.with_result(text[:_MAX_MCP_RESULT_CHARS] + notice)

    # -- on_run_error -------------------------------------------------------
    async def record_run_failure(self, event: HookInvocation) -> None:
        """Record a run failure with safe metadata only — type, not message.

        The exception message can contain user input or sensitive detail, so only
        its class name is logged.
        """
        error = event.payload_as(BaseException)
        run_id = event.run_context.run_id if event.run_context else None
        logger.warning("run failed run_id=%s error_type=%s", run_id, type(error).__name__)
