from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from approval_console import ApprovalConsole
from commands import COMMANDS
from dotenv import load_dotenv
from history import InMemoryHistory
from session_approvals import ObservableSessionApprovalRepository

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.identity import tool_identity
from agent_engine.approvals.invocation import SessionApprovalScope
from agent_engine.core.spec import AgentSpec, GraphNode, SystemSpec
from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks import RunContext
from agent_manager.domain import Role

BASE_DIR = Path(__file__).resolve().parent
USER_ID = "interactive-user"
SYSTEM_NAMESPACE = "Interactive MCP Control"


def new_session_id() -> str:
    return f"session-{uuid4().hex[:8]}"


class InteractiveApp:
    def __init__(
        self, engine: LangGraphEngine, approvals: ObservableSessionApprovalRepository
    ) -> None:
        self.engine = engine
        self.approvals = approvals
        self.history = InMemoryHistory()
        self.console = ApprovalConsole()
        self.session_id = new_session_id()

    async def run(self) -> None:
        self._startup()
        while True:
            try:
                text = (await asyncio.to_thread(input, "\nYou: ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nClosing interactive MCP control app.")
                return
            if not text:
                continue
            if text.startswith("/"):
                if await self._command(text.lower()):
                    return
                continue
            await self._ask(text)

    def _startup(self) -> None:
        discovered = self.engine.discovered_mcp_tools()
        print("\nInteractive MCP Control App")
        print(f"[SESSION STARTED] session_id={self.session_id}")
        print("Connected MCP servers:")
        for server, tools in discovered.items():
            state = "CONNECTED" if tools else "UNAVAILABLE"
            required_env = {"github": "GITHUB_TOKEN", "context7": "CONTEXT7_API_KEY"}.get(server)
            reason = (
                f" (missing {required_env})"
                if not tools and required_env and not os.getenv(required_env)
                else ""
            )
            print(f"- [{state}] {server}: {len(tools)} tools{reason}")
        print("\nType /help for commands. Type /exit to quit.")

    async def _ask(self, text: str) -> None:
        run_id = uuid4().hex
        print(f"\n[RUN] session_id={self.session_id} run_id={run_id}")
        try:
            result = await self.engine.run(
                self.history.prompt(self.session_id, text),
                context=RunContext(
                    run_id=run_id,
                    conversation_id=self.session_id,
                    user_id=USER_ID,
                ),
            )
            while result.pending_approval is not None:
                pending = result.pending_approval
                requested_tool = tool_identity(
                    provider=pending.provider,
                    server_id=pending.server_id,
                    tool_name=pending.tool_name,
                )
                decision = await self.console.decide(pending)
                print(
                    f"[APPROVAL DECISION] session_id={self.session_id} "
                    f"tool={requested_tool} decision={decision.value} source=user"
                )
                result = await self.engine.resume(
                    run_id,
                    pending.approval_id,
                    decision,
                    caller_user_id=USER_ID,
                )
                if decision == ApprovalDecision.DENY:
                    print(
                        f"[TOOL EXECUTION] session_id={self.session_id} "
                        f"tool={requested_tool} executed=false"
                    )
            self.history.append(self.session_id, Role.USER, text)
            self.history.append(self.session_id, Role.ASSISTANT, result.answer)
            self.history.record_tools(
                self.session_id,
                [
                    f"{tool.server_id or tool.provider}.{tool.name} status={tool.status}"
                    for tool in result.used_tools
                ],
            )
            print(f"\nAssistant:\n{result.answer}")
        except Exception as exc:
            print(f"\n[ERROR] {exc}")
            if os.getenv("EXTRA_MCP_DEBUG") == "1":
                traceback.print_exc()

    async def _command(self, command: str) -> bool:
        if command in {"/exit", "/quit"}:
            return True
        if command == "/help":
            print(COMMANDS)
        elif command == "/tools":
            for server, tools in self.engine.discovered_mcp_tools().items():
                print(f"{server}:")
                for tool in tools:
                    print(f"- {tool}")
        elif command == "/history":
            messages = self.history.list(self.session_id)
            if not messages:
                print("History is empty.")
            for message in messages:
                print(f"{message.role.value}: {message.content}")
            for event in self.history.tool_events(self.session_id):
                print(f"tool: {event}")
        elif command == "/approvals":
            keys = await self.approvals.list_session(self._scope())
            if not keys:
                print("No tools approved for this session.")
            for key in keys:
                print(f"- {key.tool_identity} (agent={key.agent_id})")
        elif command == "/session":
            history_size = len(self.history.list(self.session_id))
            print(f"session={self.session_id} user={USER_ID} history={history_size}")
        elif command == "/new-session":
            previous_session = self.session_id
            await self.approvals.clear_session(self._scope())
            self.session_id = new_session_id()
            print(f"[SESSION CLOSED] session_id={previous_session} approvals_cleared=true")
            print(f"[SESSION STARTED] session_id={self.session_id}")
        elif command == "/clear":
            self.history.clear(self.session_id)
            print("History cleared. Session approvals were preserved.")
        elif command == "/clear-approvals":
            await self.approvals.clear_session(self._scope())
            print("Session approvals cleared.")
        else:
            print(f"Unknown command: {command}. Type /help.")
        return False

    def _scope(self) -> SessionApprovalScope:
        return SessionApprovalScope(
            session_id=self.session_id,
            system_namespace=SYSTEM_NAMESPACE,
            user_id=USER_ID,
        )


def load_spec(*, auto: bool) -> SystemSpec:
    path = BASE_DIR / "agent.yaml"
    spec = YAMLParser().parse(str(path))
    errors = SystemSpecValidator().validate(spec, BASE_DIR)
    if errors:
        raise RuntimeError("Invalid config: " + "; ".join(map(str, errors)))
    if auto:
        assert isinstance(spec.graph.node, AgentSpec)
        spec = replace(spec, graph=GraphNode(node=replace(spec.graph.node, auto_mode=True)))
    return spec


async def async_main(*, auto: bool) -> None:
    for env_path in (BASE_DIR / ".env", BASE_DIR.parent / ".env", BASE_DIR.parents[1] / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            break
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("Missing required environment variable: ANTHROPIC_API_KEY")
    approvals = ObservableSessionApprovalRepository()
    async with LangGraphEngine(BASE_DIR, session_approval_repository=approvals) as engine:
        await engine.build(load_spec(auto=auto))
        await InteractiveApp(engine, approvals).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive real-MCP HITL control app")
    parser.add_argument("--auto", action="store_true", help="Run the agent with auto: true")
    parser.add_argument(
        "--debug", action="store_true", help="Print stack traces for runtime errors"
    )
    args = parser.parse_args()
    if args.debug:
        os.environ["EXTRA_MCP_DEBUG"] = "1"
    try:
        asyncio.run(async_main(auto=args.auto))
    except KeyboardInterrupt:
        print("\nInterrupted. Resources closed.")
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
