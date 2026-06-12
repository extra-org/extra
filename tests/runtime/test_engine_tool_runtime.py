"""Engine ownership tests for the tool runtime boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from typer.testing import CliRunner

from agentplatform.cli import main as cli_main
from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.engine import Engine, EngineRunError, RunResult
from agentplatform.runtime.mcp_manager import MCPClientProtocol
from agentplatform.runtime.tool_models import MCPToolDefinition, ToolUsageRecord
from agentplatform.runtime.tool_registry import ToolRegistry
from agentplatform.spec import load_spec
from agentplatform.spec.models import McpSpec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "agents.yml"


class FakeApp:
    def invoke(self, state: dict[str, object]) -> dict[str, object]:
        return {
            "visited": ["main_router", "main_router/flights_router"],
            "answer": f"echo:{state['message']}",
        }


class FakeMCPClient:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.connect_thread_id: int | None = None
        self.close_thread_id: int | None = None

    async def connect(self) -> None:
        import threading

        self.connected = True
        self.connect_thread_id = threading.get_ident()

    async def close(self) -> None:
        import threading

        self.closed = True
        self.close_thread_id = threading.get_ident()

    async def list_tools(self) -> list[MCPToolDefinition]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        return {"tool": name, "arguments": arguments}


def _fake_clients() -> dict[str, FakeMCPClient]:
    return {
        "flights_mcp": FakeMCPClient(),
        "super_mcp": FakeMCPClient(),
    }


def _factory(clients: dict[str, FakeMCPClient]) -> Any:
    def factory(server_id: str, config: McpSpec) -> MCPClientProtocol:
        return clients[server_id]

    return factory


def _engine(monkeypatch: Any, clients: dict[str, FakeMCPClient]) -> Engine:
    monkeypatch.setattr(
        "agentplatform.runtime.engine.build_langgraph",
        lambda *args, **kwargs: FakeApp(),
    )
    return Engine(load_spec(EXAMPLE), mcp_client_factory=_factory(clients))


def test_engine_creates_and_exposes_one_mcp_manager(monkeypatch: Any) -> None:
    engine = _engine(monkeypatch, _fake_clients())

    assert engine.mcp_manager is engine.mcp_manager


def test_engine_creates_and_exposes_one_tool_registry(monkeypatch: Any) -> None:
    engine = _engine(monkeypatch, _fake_clients())

    assert isinstance(engine.tool_registry, ToolRegistry)
    assert engine.tool_registry is engine.tool_registry


def test_engine_run_does_not_automatically_start_mcp_manager(monkeypatch: Any) -> None:
    clients = _fake_clients()
    engine = _engine(monkeypatch, clients)

    result = engine.run("hello")

    assert result == RunResult(
        system_name="Rami Levy AI System",
        visited=["main_router", "main_router/flights_router"],
        answer="echo:hello",
    )
    assert clients["flights_mcp"].connected is False
    assert clients["super_mcp"].connected is False


def test_engine_run_exposes_tool_usage_from_graph_state(monkeypatch: Any) -> None:
    class ToolUsingApp:
        def invoke(self, state: dict[str, object]) -> dict[str, object]:
            return {
                "visited": ["main_router", "main_router/super_agent"],
                "answer": "done",
                "used_tools": [
                    ToolUsageRecord(
                        name="ask_question",
                        provider="mcp",
                        status="succeeded",
                        agent_id="super_agent",
                        server_id="deepwiki",
                    )
                ],
            }

    monkeypatch.setattr(
        "agentplatform.runtime.engine.build_langgraph",
        lambda *args, **kwargs: ToolUsingApp(),
    )
    engine = Engine(load_spec(EXAMPLE), mcp_client_factory=_factory(_fake_clients()))

    result = engine.run("hello")

    assert result.used_tools == (
        ToolUsageRecord(
            name="ask_question",
            provider="mcp",
            status="succeeded",
            agent_id="super_agent",
            server_id="deepwiki",
        ),
    )


def test_engine_run_error_exposes_partial_tool_usage(monkeypatch: Any) -> None:
    class FailingToolApp:
        def invoke(self, state: dict[str, object]) -> dict[str, object]:
            used_tools = state["used_tools"]
            assert isinstance(used_tools, list)
            used_tools.append(
                ToolUsageRecord(
                    name="ask_question",
                    provider="mcp",
                    status="failed",
                    agent_id="deepwiki_agent",
                    server_id="deepwiki",
                    error="tool failed",
                )
            )
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "agentplatform.runtime.engine.build_langgraph",
        lambda *args, **kwargs: FailingToolApp(),
    )
    engine = Engine(load_spec(EXAMPLE), mcp_client_factory=_factory(_fake_clients()))

    try:
        engine.run("hello")
    except EngineRunError as exc:
        assert str(exc) == "boom"
        assert exc.used_tools == (
            ToolUsageRecord(
                name="ask_question",
                provider="mcp",
                status="failed",
                agent_id="deepwiki_agent",
                server_id="deepwiki",
                error="tool failed",
            ),
        )
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("EngineRunError was not raised")


def test_engine_start_starts_mcp_manager(monkeypatch: Any) -> None:
    clients = _fake_clients()
    engine = _engine(monkeypatch, clients)

    engine.start()

    assert clients["flights_mcp"].connected is True
    assert clients["super_mcp"].connected is True


def test_engine_stop_stops_mcp_manager(monkeypatch: Any) -> None:
    clients = _fake_clients()
    engine = _engine(monkeypatch, clients)
    engine.start()

    engine.stop()

    assert clients["flights_mcp"].closed is True
    assert clients["super_mcp"].closed is True


def test_engine_start_and_stop_use_same_async_thread(monkeypatch: Any) -> None:
    clients = _fake_clients()
    engine = _engine(monkeypatch, clients)

    engine.start()
    engine.stop()

    assert clients["flights_mcp"].connect_thread_id is not None
    assert clients["flights_mcp"].connect_thread_id == clients["flights_mcp"].close_thread_id


def test_execution_context_does_not_contain_mcp_connections() -> None:
    ctx = ExecutionContext(message="hello", state={})

    assert not hasattr(ctx, "mcp_manager")
    assert not hasattr(ctx, "mcp_client")
    assert not hasattr(ctx, "mcp_connections")


def test_cli_run_still_works_without_starting_real_mcp_client(monkeypatch: Any) -> None:
    class FakeEngine:
        calls: ClassVar[list[str]] = []

        def __init__(self, loaded: object) -> None:
            self.loaded = loaded

        def start(self) -> None:
            self.calls.append("start")

        def run(self, message: str) -> RunResult:
            self.calls.append("run")
            return RunResult(
                system_name="Rami Levy AI System",
                visited=["main_router", "main_router/flights_router"],
                answer=f"echo:{message}",
            )

        def stop(self) -> None:
            self.calls.append("stop")

    monkeypatch.setattr(cli_main, "Engine", FakeEngine)
    result = CliRunner().invoke(cli_main.app, ["run", str(EXAMPLE), "hello"])

    assert result.exit_code == 0
    assert "echo:hello" in result.stdout
    assert "route  : main_router" in result.stderr
    assert "tools used: none" in result.stderr
    assert FakeEngine.calls == ["start", "run", "stop"]


def test_cli_run_prints_tool_usage(monkeypatch: Any) -> None:
    class FakeEngine:
        calls: ClassVar[list[str]] = []

        def __init__(self, loaded: object) -> None:
            self.loaded = loaded

        def start(self) -> None:
            self.calls.append("start")

        def run(self, message: str) -> RunResult:
            self.calls.append("run")
            return RunResult(
                system_name="DeepWiki Repository Research Smoke Test",
                visited=["deepwiki_agent"],
                answer=f"echo:{message}",
                used_tools=(
                    ToolUsageRecord(
                        name="ask_question",
                        provider="mcp",
                        status="succeeded",
                        agent_id="deepwiki_agent",
                        server_id="deepwiki",
                    ),
                    ToolUsageRecord(
                        name="book_flight",
                        provider="local",
                        status="failed",
                        agent_id="deepwiki_agent",
                        error="tool failed",
                    ),
                ),
            )

        def stop(self) -> None:
            self.calls.append("stop")

    monkeypatch.setattr(cli_main, "Engine", FakeEngine)
    result = CliRunner().invoke(cli_main.app, ["run", str(EXAMPLE), "hello"])

    assert result.exit_code == 0
    assert "tools used:" in result.stderr
    assert "* ask_question [mcp: deepwiki] succeeded" in result.stderr
    assert "* book_flight [local] failed: tool failed" in result.stderr
    assert FakeEngine.calls == ["start", "run", "stop"]


def test_cli_run_prints_tool_usage_when_engine_run_fails(monkeypatch: Any) -> None:
    class FailingEngine:
        calls: ClassVar[list[str]] = []

        def __init__(self, loaded: object) -> None:
            self.loaded = loaded

        def start(self) -> None:
            self.calls.append("start")

        def run(self, message: str) -> RunResult:
            self.calls.append("run")
            raise EngineRunError(
                "boom",
                used_tools=(
                    ToolUsageRecord(
                        name="ask_question",
                        provider="mcp",
                        status="failed",
                        agent_id="deepwiki_agent",
                        server_id="deepwiki",
                        error="tool failed",
                    ),
                ),
            )

        def stop(self) -> None:
            self.calls.append("stop")

    monkeypatch.setattr(cli_main, "Engine", FailingEngine)
    result = CliRunner().invoke(cli_main.app, ["run", str(EXAMPLE), "hello"])

    assert result.exit_code == 1
    assert "* ask_question [mcp: deepwiki] failed: tool failed" in result.stderr
    assert "Runtime error: boom" in result.stderr
    assert FailingEngine.calls == ["start", "run", "stop"]


def test_cli_run_stops_engine_when_run_raises(monkeypatch: Any) -> None:
    class FailingEngine:
        calls: ClassVar[list[str]] = []

        def __init__(self, loaded: object) -> None:
            self.loaded = loaded

        def start(self) -> None:
            self.calls.append("start")

        def run(self, message: str) -> RunResult:
            self.calls.append("run")
            raise RuntimeError("boom")

        def stop(self) -> None:
            self.calls.append("stop")

    monkeypatch.setattr(cli_main, "Engine", FailingEngine)
    result = CliRunner().invoke(cli_main.app, ["run", str(EXAMPLE), "hello"])

    assert result.exit_code == 1
    assert "Runtime error: boom" in result.stderr
    assert FailingEngine.calls == ["start", "run", "stop"]
