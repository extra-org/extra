"""Engine ownership tests for the tool runtime boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from typer.testing import CliRunner

from agentplatform.cli import main as cli_main
from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.engine import Engine, RunResult
from agentplatform.runtime.mcp_manager import MCPClientProtocol
from agentplatform.runtime.tool_models import MCPToolDefinition
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

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

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
    assert FakeEngine.calls == ["start", "run", "stop"]


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
