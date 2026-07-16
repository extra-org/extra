from __future__ import annotations

import argparse
import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import demo_state
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.identity import tool_identity
from agent_engine.approvals.invocation import SessionApprovalKey
from agent_engine.approvals.session_store import InMemorySessionApprovalRepository
from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.models.factory import build_chat_model
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks import RunContext

EXAMPLE_DIR = Path(__file__).resolve().parent
TOOL_NAME = "write_demo_message"
USER_ID = "demo-user"


class DemoChatModel:
    """Deterministic model adapter; all HITL behavior remains in the real engine."""

    def __init__(self, model_name: str, tool_names: Iterable[str] = ()) -> None:
        self._model_name = model_name
        self._tool_names = tuple(tool_names)

    def bind_tools(self, tools: list[Any]) -> DemoChatModel:
        return DemoChatModel(self._model_name, (tool.name for tool in tools))

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            result = next(
                message.content
                for message in reversed(messages)
                if isinstance(message, ToolMessage)
            )
            return AIMessage(content=f"Demo completed with result: {result}")

        request = next(
            (str(message.content) for message in messages if isinstance(message, HumanMessage)), ""
        )
        call_suffix = hashlib.sha256(
            f"{self._model_name}:{self._tool_names}:{request}".encode()
        ).hexdigest()[:16]
        target = next(
            (name for name in self._tool_names if request.startswith(f"Use {name} ")),
            None,
        )
        if target is not None:
            return AIMessage(
                content="",
                tool_calls=[
                    ToolCall(name=target, args={"message": request}, id=f"route-{call_suffix}")
                ],
            )

        if TOOL_NAME not in self._tool_names:
            return AIMessage(content="The demo tool is unavailable.")
        return AIMessage(
            content="",
            tool_calls=[
                ToolCall(
                    name=TOOL_NAME,
                    args={"message": request},
                    id=f"tool-{call_suffix}",
                )
            ],
        )


def demo_model_factory(
    provider: str, name: str, temperature: float | None, **_: Any
) -> BaseChatModel:
    del provider, temperature
    return cast(BaseChatModel, DemoChatModel(name))


def real_provider_model_factory(
    provider: str, name: str, temperature: float | None, **kwargs: Any
) -> BaseChatModel:
    """Keep routing repeatable while selected agents use the real provider."""
    if name == "demo-router":
        return cast(BaseChatModel, DemoChatModel(name))
    return build_chat_model(provider, name, temperature, **kwargs)


class DemoRunner:
    def __init__(
        self,
        engine: LangGraphEngine,
        repository: InMemorySessionApprovalRepository,
        *,
        interactive: bool = False,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.interactive = interactive
        self.approval_requests = 0

    async def invoke(
        self,
        *,
        agent_id: str,
        session_id: str,
        message: str,
        scripted_decision: ApprovalDecision | None,
    ) -> None:
        run_id = f"demo-{uuid4().hex}"
        key = SessionApprovalKey(
            session_id=session_id,
            agent_id=agent_id,
            tool_identity=tool_identity(provider="local", server_id=None, tool_name=TOOL_NAME),
            system_namespace="HITL Approval Demo",
            user_id=USER_ID,
        )
        permission_found = await self.repository.is_allowed(key)
        print("\n=== INVOCATION ===")
        print(f"agent_id: {agent_id}")
        print(f"session_id: {session_id}")
        print(f"tool_id: local.{TOOL_NAME}")
        print(f"run_id: {run_id}")
        print(f"session_permission_found: {permission_found}")
        engine_input = f"Use {agent_id} to write: {message}"
        print(f"engine_input: {engine_input}")

        result = await self.engine.run(
            engine_input,
            context=RunContext(
                run_id=run_id,
                conversation_id=session_id,
                user_id=USER_ID,
            ),
        )
        if result.pending_approval is not None:
            self.approval_requests += 1
            pending = result.pending_approval
            print(f"model_tool_request_observed: {pending.tool_name}")
            print("approval_required: true")
            print(f"approval_id: {pending.approval_id}")
            decision = scripted_decision
            if self.interactive:
                decision = self._prompt(pending.agent_id, pending.tool_name, pending.arguments)
            if decision is None:
                raise RuntimeError("A scripted decision is required for this scenario")
            print(f"decision: {decision.value}")
            result = await self.engine.resume(
                run_id,
                pending.approval_id,
                decision,
                caller_user_id=USER_ID,
            )
        else:
            print(f"model_tool_request_observed: {bool(result.used_tools)}")
            print("approval_required: false")
            print("approval_prompt_skipped: true")

        saved = await self.repository.is_allowed(key)
        if saved and not permission_found:
            print("Session approval saved:")
            print(f"session_id: {session_id}")
            print(f"agent_id: {agent_id}")
            print(f"tool_id: local.{TOOL_NAME}")
        print(f"tool_executed: {bool(result.used_tools)}")
        print(f"result_status: {result.status}")

    @staticmethod
    def _prompt(agent_id: str, tool_name: str, arguments: dict[str, Any]) -> ApprovalDecision:
        print("\nTool approval required")
        print(f"Agent: {agent_id}")
        print(f"Tool: local.{tool_name}")
        print(f"Argument keys: {', '.join(sorted(arguments))}")
        print("1. Allow once")
        print("2. Allow for this session")
        print("3. Deny")
        choices = {
            "1": ApprovalDecision.ALLOW_ONCE,
            "2": ApprovalDecision.ALLOW_FOR_SESSION,
            "3": ApprovalDecision.DENY,
        }
        while (choice := input("Choose an action: ").strip()) not in choices:
            print("Please enter 1, 2, or 3.")
        return choices[choice]

    def summary(self) -> None:
        print("\n=== SUMMARY ===")
        print(f"approval_requests: {self.approval_requests}")
        print(f"tool_executions: {demo_state.execution_count}")


async def run_scenario(
    name: str, *, interactive: bool = False, real_provider: bool = False
) -> None:
    demo_state.reset()
    config_path = EXAMPLE_DIR / "agents.yml"
    spec = YAMLParser().parse(str(config_path))
    errors = SystemSpecValidator().validate(spec, EXAMPLE_DIR)
    if errors:
        raise RuntimeError("Invalid example config: " + "; ".join(map(str, errors)))

    repository = InMemorySessionApprovalRepository()
    engine_options: dict[str, Any] = {"session_approval_repository": repository}
    engine_options["model_factory"] = (
        real_provider_model_factory if real_provider else demo_model_factory
    )
    async with LangGraphEngine(EXAMPLE_DIR, **engine_options) as engine:
        await engine.build(spec)
        runner = DemoRunner(engine, repository, interactive=interactive)
        await _execute_scenario(runner, name)
        runner.summary()


async def _execute_scenario(runner: DemoRunner, name: str) -> None:
    if name == "allow-once":
        for index in (1, 2):
            await runner.invoke(
                agent_id="approval_demo_agent",
                session_id="demo-session-1",
                message=f"demo message {index}",
                scripted_decision=ApprovalDecision.ALLOW_ONCE,
            )
    elif name == "allow-session":
        for index in (1, 2):
            await runner.invoke(
                agent_id="approval_demo_agent",
                session_id="demo-session-2",
                message=f"session demo message {index}",
                scripted_decision=(ApprovalDecision.ALLOW_FOR_SESSION if index == 1 else None),
            )
    elif name == "new-session":
        await runner.invoke(
            agent_id="approval_demo_agent",
            session_id="demo-session-2",
            message="first session boundary message",
            scripted_decision=ApprovalDecision.ALLOW_FOR_SESSION,
        )
        await runner.invoke(
            agent_id="approval_demo_agent",
            session_id="demo-session-3",
            message="second session boundary message",
            scripted_decision=ApprovalDecision.ALLOW_ONCE,
        )
    elif name == "different-agent":
        await runner.invoke(
            agent_id="approval_demo_agent",
            session_id="demo-session-agent-scope",
            message="first agent boundary message",
            scripted_decision=ApprovalDecision.ALLOW_FOR_SESSION,
        )
        await runner.invoke(
            agent_id="second_approval_demo_agent",
            session_id="demo-session-agent-scope",
            message="second agent boundary message",
            scripted_decision=ApprovalDecision.ALLOW_ONCE,
        )
    elif name == "deny":
        for message, decision in (
            ("first controlled call", ApprovalDecision.DENY),
            ("second controlled call", ApprovalDecision.DENY),
        ):
            await runner.invoke(
                agent_id="approval_demo_agent",
                session_id="demo-session-deny",
                message=message,
                scripted_decision=decision,
            )
    elif name == "auto":
        await runner.invoke(
            agent_id="auto_demo_agent",
            session_id="demo-session-auto",
            message="automatic call",
            scripted_decision=None,
        )
    elif name == "interactive":
        await runner.invoke(
            agent_id="approval_demo_agent",
            session_id="demo-session-interactive",
            message="interactive call",
            scripted_decision=None,
        )
    else:
        raise ValueError(f"Unknown scenario: {name}")


def main() -> None:
    scenarios = (
        "allow-once",
        "allow-session",
        "new-session",
        "different-agent",
        "deny",
        "auto",
        "interactive",
        "all",
    )
    parser = argparse.ArgumentParser(description="Run the real engine HITL approval demo")
    parser.add_argument("scenario", choices=scenarios)
    parser.add_argument(
        "--real-provider",
        action="store_true",
        help="Use the Anthropic API instead of the deterministic model adapter",
    )
    args = parser.parse_args()
    if args.real_provider:
        for env_path in (
            EXAMPLE_DIR / ".env",
            EXAMPLE_DIR.parent / ".env",
            EXAMPLE_DIR.parents[1] / ".env",
        ):
            if env_path.is_file():
                load_dotenv(env_path, override=False)
                break
    if args.scenario == "all":
        for scenario in scenarios[:-2]:
            print(f"\n\n######## SCENARIO: {scenario} ########")
            asyncio.run(run_scenario(scenario, real_provider=args.real_provider))
    else:
        asyncio.run(
            run_scenario(
                args.scenario,
                interactive=args.scenario == "interactive",
                real_provider=args.real_provider,
            )
        )


if __name__ == "__main__":
    main()
