"""Interactive real-LLM auto-mode proof using the local knowledge MCP."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.identity import tool_identity
from agent_engine.approvals.invocation import (
    SessionApprovalGrant,
    SessionApprovalKey,
    SessionApprovalScope,
)
from agent_engine.approvals.session_store import InMemorySessionApprovalRepository
from agent_engine.core.spec import AgentSpec, GraphNode, SystemSpec
from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.types import PendingApproval, RunResult
from agent_engine.models.factory import ModelConfigurationError, build_chat_model
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks import RunContext
from agent_engine.runtime.tool_models import ToolUsageRecord
from agent_manager.application import ConversationService, PreparedConversationTurn
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository

EXAMPLE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = EXAMPLE_DIR / "local_mcp_agents.yaml"
SERVER_ID = "local_knowledge_mcp"
SYSTEM_NAMESPACE = "Enterprise Knowledge Assistant Local MCP Auto Mode"
USER_ID = "local-demo-user"
DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
EXPECTED_TOOLS = {
    "search_internal_documents",
    "get_employee_information",
    "publish_internal_note",
}
_CREDENTIAL_FIELD_MARKERS = (
    "key",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
)

EventSink = Callable[[str], None]
class ObservableSessionApprovalRepository(InMemorySessionApprovalRepository):
    """Emit safe cache and persistence events without arguments or credentials."""

    def __init__(self, *, emit: EventSink = print) -> None:
        super().__init__()
        self._emit = emit

    async def is_allowed(self, key: SessionApprovalKey) -> bool:
        allowed = await super().is_allowed(key)
        self._emit(
            f"[APPROVAL CACHE] session_id={key.session_id} "
            f"tool={key.tool_identity} source=session_cache "
            f"hit={str(allowed).lower()}"
        )
        return allowed

    async def allow(
        self,
        key: SessionApprovalKey,
        *,
        grant: SessionApprovalGrant | None = None,
    ) -> None:
        await super().allow(key, grant=grant)
        self._emit(
            f"[APPROVAL STORED] session_id={key.session_id} "
            f"tool={key.tool_identity} decision=allow_for_session"
        )


def configured_model_factory(
    provider: str,
    name: str,
    temperature: float | None,
    **kwargs: Any,
) -> BaseChatModel:
    """Build the real YAML-configured model with an actionable safe failure."""
    try:
        model = build_chat_model(
            provider,
            name,
            temperature,
            region=kwargs.get("region"),
            max_tokens=kwargs.get("max_tokens"),
            top_p=kwargs.get("top_p"),
        )
        missing_environment = _missing_model_environment(model)
        if missing_environment:
            raise ModelConfigurationError(
                "Missing configured model credential environment: "
                + ", ".join(missing_environment)
                + f". Set it in {EXAMPLE_DIR / '.env'} (see .env.example), "
                "or export it before starting the runner."
            )
        return model
    except ModelConfigurationError:
        raise
    except Exception as exc:
        raise ModelConfigurationError(
            f"Could not initialize configured model provider '{provider}' "
            f"with model '{name}'. Configure that provider's existing environment "
            f"credentials in {EXAMPLE_DIR / '.env'} (see .env.example), or export "
            "the standard provider variables before starting the runner."
        ) from exc


def _missing_model_environment(model: BaseChatModel) -> tuple[str, ...]:
    """Use LangChain's secret metadata without reading or logging secret values."""
    secret_fields = getattr(model, "lc_secrets", {}) or {}
    missing: list[str] = []
    for field_name, environment_name in secret_fields.items():
        lowered = field_name.casefold()
        if not any(marker in lowered for marker in _CREDENTIAL_FIELD_MARKERS):
            continue
        value = getattr(model, field_name, None)
        if value is None:
            # Some providers intentionally defer to an ambient credential chain
            # (for example a profile or workload identity) rather than a field.
            continue
        get_secret_value = getattr(value, "get_secret_value", None)
        if callable(get_secret_value):
            value = get_secret_value()
        if not str(value).strip():
            missing.append(str(environment_name))
    return tuple(sorted(set(missing)))


def load_spec(*, mcp_url: str = DEFAULT_MCP_URL) -> SystemSpec:
    spec = YAMLParser().parse(str(CONFIG_PATH))
    errors = SystemSpecValidator().validate(spec, EXAMPLE_DIR)
    if errors:
        raise RuntimeError("Invalid local MCP config: " + "; ".join(map(str, errors)))
    if not isinstance(spec.graph.node, AgentSpec):
        raise RuntimeError("The local MCP approval config must have an agent root")
    agent = spec.graph.node
    mcps = tuple(replace(mcp, url=mcp_url) if mcp.id == SERVER_ID else mcp for mcp in agent.mcps)
    return replace(spec, graph=GraphNode(node=replace(agent, mcps=mcps)))


def load_model_environment(env_path: str | None) -> Path:
    resolved = Path(env_path).expanduser().resolve() if env_path else EXAMPLE_DIR / ".env"
    load_dotenv(resolved, override=True)
    return resolved


class ApprovalDemo:
    def __init__(
        self,
        engine: LangGraphEngine,
        repository: ObservableSessionApprovalRepository,
        conversations: ConversationService,
        *,
        provider: str,
        model_name: str,
        session_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._conversations = conversations
        self._provider = provider
        self._model_name = model_name
        self.session_id = session_id or _new_session_id()

    def show_startup(self) -> None:
        discovered = self._engine.discovered_mcp_tools().get(SERVER_ID, ())
        print(f"[SESSION STARTED] session_id={self.session_id}")
        print(f"[MODEL CONFIGURED] provider={self._provider} model={self._model_name}")
        print(f"[MCP DISCOVERED] server={SERVER_ID} tools={','.join(discovered) or '(none)'}")
        missing = sorted(EXPECTED_TOOLS - set(discovered))
        if missing:
            raise RuntimeError(f"Local MCP did not expose required tools: {', '.join(missing)}")

    async def invoke(self, user_message: str) -> RunResult:
        messages_before = await self._conversations.history(self.session_id)
        print(
            f"[SESSION HISTORY] session_id={self.session_id} "
            f"messages_before_run={len(messages_before)}"
        )
        print(
            f"[USER MESSAGE] session_id={self.session_id} text={_safe_user_message(user_message)}"
        )
        turn = await self._conversations.prepare_turn(
            self.session_id,
            user_message,
            user_id=USER_ID,
        )
        print(f"[SESSION MESSAGE APPENDED] session_id={self.session_id} role=user")
        print(
            f"[MODEL CONTEXT] session_id={self.session_id} "
            f"message_count={len(turn.history) + 1}"
        )
        self._log_model_invocation(turn.run_id, phase="started")
        result = await self._engine.run(
            turn.message,
            history=turn.history,
            context=RunContext(
                run_id=turn.run_id,
                conversation_id=self.session_id,
                user_id=USER_ID,
            ),
        )

        prompted: list[tuple[str, str, ApprovalDecision]] = []
        while result.pending_approval is not None:
            pending = result.pending_approval
            identity = _pending_identity(pending)
            self._log_selection(pending.server_id, pending.tool_name, identity)
            print(
                f"[APPROVAL] session_id={self.session_id} "
                f"server={pending.server_id or '-'} tool={pending.tool_name} "
                "requested=true source=user"
            )
            decision = await _prompt_for_decision(pending)
            prompted.append((identity, pending.tool_name, decision))
            print(
                f"[APPROVAL DECISION] session_id={self.session_id} "
                f"tool={identity} decision={decision.value} source=user"
            )
            self._log_model_invocation(turn.run_id, phase="resuming_after_decision")
            result = await self._engine.resume(
                turn.run_id,
                pending.approval_id,
                decision,
                caller_user_id=USER_ID,
            )

        await self._complete_turn(turn, result)
        self._log_completed_tools(result, prompted)
        self._log_model_invocation(turn.run_id, phase="completed")
        print(f"[FINAL ASSISTANT RESPONSE] session_id={self.session_id}")
        print(result.answer)
        return result

    async def _complete_turn(
        self,
        turn: PreparedConversationTurn,
        result: RunResult,
    ) -> None:
        await self._conversations.complete_turn(turn, result)
        print(f"[SESSION MESSAGE APPENDED] session_id={self.session_id} role=assistant")

    async def new_session(self, *, session_id: str | None = None) -> None:
        previous = self.session_id
        await self._repository.clear_session(
            SessionApprovalScope(
                session_id=previous,
                system_namespace=SYSTEM_NAMESPACE,
                user_id=USER_ID,
            )
        )
        self.session_id = await self._conversations.create(
            user_id=USER_ID,
            session_id=session_id or _new_session_id(),
        )
        print(f"[SESSION CLOSED] session_id={previous} approvals_cleared=true")
        print(f"[SESSION STARTED] session_id={self.session_id}")
        print(f"[SESSION HISTORY] session_id={self.session_id} messages_before_run=0")

    async def interactive_loop(self) -> None:
        print("Ask a natural-language question, or enter /new-session, /session, /tools, /exit.")
        while True:
            try:
                user_message = (await asyncio.to_thread(input, "\nYou: ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nClosing local MCP approval demo.")
                return
            if not user_message:
                continue
            if user_message in {"/exit", "/quit"}:
                return
            if user_message == "/new-session":
                await self.new_session()
                continue
            if user_message == "/session":
                print(f"[SESSION] session_id={self.session_id}")
                continue
            if user_message == "/tools":
                for tool_name in sorted(EXPECTED_TOOLS):
                    print(tool_name)
                continue
            try:
                await self.invoke(user_message)
            except Exception as exc:
                print(f"[RUN ERROR] error_type={type(exc).__name__}")

    def _log_model_invocation(self, run_id: str, *, phase: str) -> None:
        print(
            f"[MODEL INVOCATION] session_id={self.session_id} run_id={run_id} "
            f"provider={self._provider} model={self._model_name} phase={phase}"
        )

    def _log_selection(
        self,
        server_id: str | None,
        tool_name: str,
        identity: str,
    ) -> None:
        print(
            f"[MODEL TOOL SELECTION] session_id={self.session_id} "
            f"server={server_id or '-'} tool={tool_name} identity={identity}"
        )

    def _log_completed_tools(
        self,
        result: RunResult,
        prompted: list[tuple[str, str, ApprovalDecision]],
    ) -> None:
        prompted_identities = {identity for identity, _, _ in prompted}
        usage_identities: set[str] = set()
        for usage in result.used_tools:
            identity = _usage_identity(usage)
            usage_identities.add(identity)
            if identity not in prompted_identities:
                self._log_selection(usage.server_id, usage.name, identity)
                print(
                    f"[APPROVAL] session_id={self.session_id} "
                    f"server={usage.server_id or '-'} tool={usage.name} "
                    "requested=false source=session_cache"
                )
            print(
                f"[TOOL EXECUTION] session_id={self.session_id} "
                f"server={usage.server_id or '-'} tool={usage.name} "
                f"executed=true status={usage.status}"
            )
            print(
                f"[TOOL RESULT] session_id={self.session_id} tool={identity} "
                "returned_to_llm=true kind=mcp_result"
            )

        for identity, tool_name, decision in prompted:
            if decision == ApprovalDecision.DENY and identity not in usage_identities:
                print(
                    f"[TOOL EXECUTION] session_id={self.session_id} server={SERVER_ID} "
                    f"tool={tool_name} executed=false status=denied"
                )
                print(
                    f"[TOOL RESULT] session_id={self.session_id} tool={identity} "
                    "returned_to_llm=true kind=safe_denial"
                )

        if not result.used_tools and not prompted:
            print(
                f"[MODEL TOOL SELECTION] session_id={self.session_id} "
                "server=- tool=(none) identity=(none)"
            )


def _pending_identity(pending: PendingApproval) -> str:
    return tool_identity(
        provider="mcp" if pending.provider == "mcp" else "local",
        server_id=pending.server_id,
        tool_name=pending.tool_name,
    )


def _usage_identity(usage: ToolUsageRecord) -> str:
    return tool_identity(
        provider=usage.provider,
        server_id=usage.server_id,
        tool_name=usage.name,
    )


def _safe_user_message(message: str) -> str:
    """Describe a user turn without printing any of its content."""
    return f"chars={len(message)} content=omitted"


async def _prompt_for_decision(pending: PendingApproval) -> ApprovalDecision:
    print("Approval required before execution.")
    print(f"Agent: {pending.agent_id}")
    print(f"MCP server: {pending.server_id}")
    print(f"Tool: {pending.tool_name}")
    print(f"Argument keys: {', '.join(sorted(pending.arguments)) or '(none)'}")
    print("1=Allow once, 2=Approve for this session, 3=Deny")
    choices = {
        "1": ApprovalDecision.ALLOW_ONCE,
        "2": ApprovalDecision.ALLOW_FOR_SESSION,
        "3": ApprovalDecision.DENY,
    }
    while True:
        raw = (await asyncio.to_thread(input, "Decision: ")).strip()
        decision = choices.get(raw)
        if decision is not None:
            return decision
        print("Enter 1, 2, or 3.")


async def async_main(*, mcp_url: str, env_path: str | None) -> None:
    load_model_environment(env_path)
    spec = load_spec(mcp_url=mcp_url)
    assert isinstance(spec.graph.node, AgentSpec)
    model = spec.graph.node.model
    repository = ObservableSessionApprovalRepository()
    conversation_repository = MemoryRepository()
    async with LangGraphEngine(
        EXAMPLE_DIR,
        model_factory=configured_model_factory,
        session_approval_repository=repository,
    ) as engine:
        await engine.build(spec)
        conversations = ConversationService(
            engine,
            conversation_repository,
            system_name=SYSTEM_NAMESPACE,
            config_path=str(CONFIG_PATH),
        )
        session_id = await conversations.create(user_id=USER_ID, session_id=_new_session_id())
        demo = ApprovalDemo(
            engine,
            repository,
            conversations,
            provider=model.provider,
            model_name=model.name,
            session_id=session_id,
        )
        demo.show_startup()
        await demo.interactive_loop()


def _new_session_id() -> str:
    return f"local-session-{uuid4().hex[:8]}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive real-LLM local MCP session-approval demonstration"
    )
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument(
        "--env",
        help="Environment file for the YAML-configured model provider "
        "(defaults to the example's .env)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(async_main(mcp_url=args.mcp_url, env_path=args.env))
    except ModelConfigurationError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
