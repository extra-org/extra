"""Extra MCP server — expose the full Extra system as an MCP tool.

The engine is built once when the server starts and reused for all requests.
Tool calls flow through the existing :class:`ConversationService` so history,
token-budget, persistence, and hooks behave exactly as they do in the CLI.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_manager.application import ConversationService
from agent_manager.composition import ApplicationRepositories, application_repositories
from agent_manager.config import Settings
from agent_manager.domain.identity import Principal
from agentctl.session import load_and_validate, load_env

DEFAULT_USER_ID = "local-user"


def _principal_for(user_id: str) -> Principal:
    """Build an anonymous :class:`Principal` for the caller-supplied id.

    The MCP caller is an external process connected over stdio, not the host
    product that vouches for real identities. ``user_id`` is therefore treated
    as an opaque conversation label rather than a host-verified external id.
    """
    return Principal.anonymous(user_id)


class ExtraMCPServer:
    """Build and run an MCP server wrapping the Extra graph."""

    def __init__(self, config: str, env: str | None) -> None:
        load_env(config, env)
        self._spec, self._base_dir = load_and_validate(config)
        self._config_path = Path(config).resolve()
        self._settings = Settings()
        self._server = FastMCP(name=self._spec.meta.name)

        self._repositories_cm = application_repositories(self._settings)
        self._repositories: ApplicationRepositories | None = None
        self._engine: LangGraphEngine | None = None
        self._service: ConversationService | None = None

        self._register_tools()

    def _register_tools(self) -> None:
        """Register ``extra_chat`` and ``decide_approval`` tools, capturing ``self``."""

        @self._server.tool()
        async def extra_chat(
            message: str,
            session_id: str = "",
            user_id: str = "",
        ) -> dict[str, Any]:
            """Send a message to the Extra agent system and return the answer.

            When ``session_id`` is omitted or empty, a new conversation is
            created automatically and its id is returned in the response. The
            same ``session_id`` can be sent again to continue the previous
            conversation; different ``session_id`` values keep their own
            history.
            """
            return await self._handle_chat(message, session_id, user_id)

        @self._server.tool()
        async def decide_approval(
            session_id: str,
            run_id: str,
            approval_id: str,
            decision: str = "approve",
        ) -> dict[str, Any]:
            """Approve or reject a pending tool-call approval.

            ``session_id`` identifies the conversation. ``run_id`` and
            ``approval_id`` come from the ``pending_approval`` block returned
            by ``extra_chat`` when the run is suspended. ``decision`` accepts
            ``approve`` (allow this invocation once), ``allow_for_session``
            (allow and stop asking for this tool in this conversation), and
            ``reject`` (do not run; store nothing).
            """
            return await self._handle_decide_approval(session_id, run_id, approval_id, decision)

    async def _setup(self) -> None:
        assert self._repositories is not None
        engine = LangGraphEngine(
            self._base_dir,
            session_approval_repository=self._repositories.session_approvals,
            tool_usage_repository=self._repositories.tool_usage,
            run_repository=self._repositories.runs,
        )
        # Assign the engine before ``build`` so a build failure still
        # triggers ``close`` in the outer ``run`` cleanup block.
        self._engine = engine
        await engine.build(self._spec)
        self._service = ConversationService(
            engine,
            self._repositories.conversations,
            window=self._settings.context_window,
            max_chars=self._settings.context_max_chars,
            max_tokens=self._settings.context_max_tokens,
            snapshot_ttl_seconds=self._settings.snapshot_ttl_seconds,
            system_name=self._spec.meta.name,
            config_path=str(self._config_path),
            run_repository=self._repositories.runs,
        )
        await self._server.run_stdio_async()

    async def _handle_chat(self, message: str, session_id: str, user_id: str) -> dict[str, Any]:
        service = self._service
        if service is None:
            raise RuntimeError("MCP server has not finished initializing")
        effective_session_id = session_id or ""
        effective_user_id = user_id or DEFAULT_USER_ID
        principal = _principal_for(effective_user_id)
        if not effective_session_id:
            effective_session_id = await service.create(principal)
        result = await service.send(effective_session_id, message, principal)
        payload: dict[str, Any] = {
            "session_id": effective_session_id,
            "status": result.status.value,
            "answer": result.answer,
            "visited": list(result.visited),
            "used_tools": [
                {k: v for k, v in asdict(tool).items() if v is not None}
                for tool in result.used_tools
            ],
        }
        if result.pending_approval is not None:
            payload["pending_approval"] = asdict(result.pending_approval)
        return payload

    async def _handle_decide_approval(
        self,
        session_id: str,
        run_id: str,
        approval_id: str,
        decision: str,
    ) -> dict[str, Any]:
        service = self._service
        if service is None:
            raise RuntimeError("MCP server has not finished initializing")
        effective_user_id = DEFAULT_USER_ID
        principal = _principal_for(effective_user_id)
        parsed_decision = parse_decision(decision, default=ApprovalDecision.DENY)
        result = await service.decide_approval(
            session_id,
            run_id,
            approval_id,
            parsed_decision,
            principal,
        )
        return {
            "session_id": session_id,
            "status": result.status.value,
            "answer": result.answer,
            "visited": list(result.visited),
            "used_tools": [
                {k: v for k, v in asdict(tool).items() if v is not None}
                for tool in result.used_tools
            ],
        }

    async def run(self) -> None:
        try:
            self._repositories = await self._repositories_cm.__aenter__()
            await self._setup()
        finally:
            if self._engine is not None:
                await self._engine.close()
            self._service = None
            self._engine = None
            if self._repositories is not None:
                await self._repositories_cm.__aexit__(None, None, None)
            self._repositories = None


def create_server(config: str, env: str | None) -> ExtraMCPServer:
    """Factory used by the CLI and tests."""
    return ExtraMCPServer(config, env)


__all__ = ["ExtraMCPServer", "create_server"]
