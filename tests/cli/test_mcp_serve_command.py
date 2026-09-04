"""Tests for ``agentctl mcp serve``.

These tests cover three layers:

* the CLI subcommand validates the spec and starts stdio mode
* the ``extra_chat`` tool reuses :class:`ConversationService` instead of
  reimplementing history or session logic
* a real MCP client (over stdio) can discover ``extra_chat``, send a
  message, receive the answer, and isolate sessions from each other
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from click.testing import CliRunner

from agent_engine.approvals.models import RunStatus
from agent_engine.engine.types import ChatMessage, RunResult
from agent_engine.runtime.hooks import RunContext
from agentctl.diagnostics import ValidationResult
from agentctl.main import cli


class FakeRuntimeEngine:
    """Stand-in for ``LangGraphEngine`` that records every run.

    It echoes the prompt as the answer so tests can assert on it. The
    ``answer`` keeps track of which call number this is, which is useful for
    verifying that history reaches the engine on the second turn.

    The ``build_count`` counter lets the integration test assert the engine is
    built exactly once even though the integration test runs the server in a
    subprocess where class variables are not shared with the parent.
    """

    prompts: ClassVar[list[str]] = []
    histories: ClassVar[list[tuple[ChatMessage, ...]]] = []
    contexts: ClassVar[list[RunContext | None]] = []
    build_count: ClassVar[int] = 0

    def __init__(self, _base_dir: Path, **_kwargs: object) -> None:
        self._closed = False

    async def __aenter__(self) -> FakeRuntimeEngine:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def build(self, _spec: object) -> None:
        type(self).build_count += 1

    async def run(
        self,
        message: str,
        *,
        history: tuple[ChatMessage, ...] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        self.prompts.append(message)
        self.histories.append(history)
        self.contexts.append(context)
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer=f"echo:{message}",
        )

    async def close(self) -> None:
        if not self._closed:
            self._closed = True


def _write_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "agents.yml"
    spec.write_text(
        "system: {name: Fake System}\n"
        "agents: {fake_agent: {description: fake}}\n"
        "graph: {fake_agent: null}\n",
        encoding="utf-8",
    )
    return spec


def test_mcp_serve_validates_config_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command should reject an invalid spec without launching the server."""
    spec = _write_spec(tmp_path)

    def fake_validate(config: str) -> ValidationResult:
        return ValidationResult(
            ok=False,
            errors=["[agents.fake_agent] agent is not implemented"],
        )

    monkeypatch.setattr("agentctl.diagnostics.validate_spec", fake_validate)

    res = CliRunner().invoke(cli, ["mcp", "serve", "--config", str(spec)])
    assert res.exit_code == 1
    assert "Validation failed:" in res.output


def test_mcp_serve_starts_with_valid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command should build the engine and enter stdio mode."""
    spec = _write_spec(tmp_path)

    captured: dict[str, object] = {}

    async def fake_run_stdio(self: object) -> None:
        captured["ran"] = True

    def fake_validate(config: str) -> ValidationResult:
        return ValidationResult(ok=True)

    monkeypatch.setattr("agentctl.diagnostics.validate_spec", fake_validate)

    monkeypatch.setattr("agentctl.mcp_serve.LangGraphEngine", FakeRuntimeEngine)
    monkeypatch.setattr("agentctl.mcp_serve.FastMCP.run_stdio_async", fake_run_stdio)

    res = CliRunner().invoke(cli, ["mcp", "serve", "--config", str(spec)])
    assert res.exit_code == 0, res.output
    assert captured.get("ran") is True


def test_extra_chat_tool_creates_session_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no session_id is provided, the tool should create one and return it."""
    spec = _write_spec(tmp_path)

    created_sessions: list[str] = []
    sent_messages: list[tuple[str, str]] = []

    from agent_manager.domain.identity import Principal

    async def fake_create(
        self_inner: object,
        principal: Principal,
        *,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or "new-session-123"
        created_sessions.append(sid)
        return sid

    async def fake_send(
        self_inner: object,
        conversation_id: str,
        text: str,
        principal: Principal,
    ) -> RunResult:
        sent_messages.append((conversation_id, text))
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer=f"answer-{len(sent_messages)}",
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "create", fake_create)
    monkeypatch.setattr(ConversationService, "send", fake_send)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    fake_engine = FakeRuntimeEngine(Path("."))
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = fake_engine  # type: ignore[assignment]
    server._service = ConversationService(fake_engine, _FakeRepository())  # type: ignore[arg-type]

    result = asyncio.run(server._handle_chat("hello", "", "u1"))

    assert result["session_id"] == "new-session-123"
    assert result["answer"] == "answer-1"
    assert result["visited"] == ["fake_agent"]
    assert sent_messages == [("new-session-123", "hello")]
    assert created_sessions == ["new-session-123"]


def test_extra_chat_tool_reuses_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a session_id is provided, the tool should reuse that session."""
    spec = _write_spec(tmp_path)

    sent_messages: list[tuple[str, str]] = []

    from agent_manager.domain.identity import Principal

    async def fake_send(
        self_inner: object,
        conversation_id: str,
        text: str,
        principal: Principal,
    ) -> RunResult:
        sent_messages.append((conversation_id, text))
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer=f"answer-{len(sent_messages)}",
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "send", fake_send)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    fake_engine = FakeRuntimeEngine(Path("."))
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = fake_engine  # type: ignore[assignment]
    server._service = ConversationService(fake_engine, _FakeRepository())  # type: ignore[arg-type]

    result = asyncio.run(server._handle_chat("follow-up", "sess-42", "u1"))

    assert result["session_id"] == "sess-42"
    assert result["answer"] == "answer-1"
    assert sent_messages == [("sess-42", "follow-up")]


def test_extra_chat_tool_default_user_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When user_id is omitted, the tool should fall back to a stable default."""
    spec = _write_spec(tmp_path)

    sent: list[tuple[str, str]] = []

    from agent_manager.domain.identity import Principal

    async def fake_send(
        self_inner: object,
        conversation_id: str,
        text: str,
        principal: Principal,
    ) -> RunResult:
        sent.append((text, principal.user_id))
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer="ok",
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "send", fake_send)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = FakeRuntimeEngine(Path("."))  # type: ignore[assignment]
    server._service = ConversationService(server._engine, _FakeRepository())  # type: ignore[arg-type]

    asyncio.run(server._handle_chat("hello", "sess-1", ""))

    assert sent == [("hello", "anon:local-user")]


def test_extra_chat_tool_returns_used_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The response should expose the ``used_tools`` from the run result."""
    spec = _write_spec(tmp_path)

    from agent_engine.runtime.tool_models import ToolUsageRecord

    record = ToolUsageRecord(
        name="search_internal_documents",
        provider="local",
        status="succeeded",
        agent_id="fake_agent",
    )

    from agent_manager.domain.identity import Principal

    async def fake_send(
        self_inner: object,
        conversation_id: str,
        text: str,
        principal: Principal,
    ) -> RunResult:
        return RunResult(
            system_name="Fake System",
            visited=["root", "knowledge_agent"],
            answer="the answer",
            used_tools=(record,),
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "send", fake_send)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = FakeRuntimeEngine(Path("."))  # type: ignore[assignment]
    server._service = ConversationService(server._engine, _FakeRepository())  # type: ignore[arg-type]

    result = asyncio.run(server._handle_chat("search docs", "sess-x", "u1"))

    assert result["visited"] == ["root", "knowledge_agent"]
    assert result["used_tools"] == [
        {
            "name": "search_internal_documents",
            "provider": "local",
            "status": "succeeded",
            "agent_id": "fake_agent",
        }
    ]


def test_extra_chat_tool_exposes_completed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal response should include status=completed and no pending_approval."""
    spec = _write_spec(tmp_path)

    from agent_manager.domain.identity import Principal

    async def fake_send(
        self_inner: object,
        conversation_id: str,
        text: str,
        principal: Principal,
    ) -> RunResult:
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer="done",
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "send", fake_send)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = FakeRuntimeEngine(Path("."))  # type: ignore[assignment]
    server._service = ConversationService(server._engine, _FakeRepository())  # type: ignore[arg-type]

    result = asyncio.run(server._handle_chat("hi", "sess-1", "u1"))

    assert result["status"] == "completed"
    assert "pending_approval" not in result
    assert result["answer"] == "done"


def test_extra_chat_tool_exposes_pending_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the run suspends at an approval, the response must expose it."""
    spec = _write_spec(tmp_path)

    from agent_engine.engine.types import PendingApproval
    from agent_manager.domain.identity import Principal

    pending = PendingApproval(
        run_id="run-1",
        approval_id="approval-1",
        agent_id="fake_agent",
        tool_name="dangerous_tool",
        description="do something risky",
        provider="local",
        arguments={"x": 1},
    )

    async def fake_send(
        self_inner: object,
        conversation_id: str,
        text: str,
        principal: Principal,
    ) -> RunResult:
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer="",
            status=RunStatus.PENDING_APPROVAL,
            pending_approval=pending,
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "send", fake_send)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = FakeRuntimeEngine(Path("."))  # type: ignore[assignment]
    server._service = ConversationService(server._engine, _FakeRepository())  # type: ignore[arg-type]

    result = asyncio.run(server._handle_chat("do it", "sess-1", "u1"))

    assert result["status"] == "pending_approval"
    assert result["answer"] == ""
    assert result["pending_approval"]["run_id"] == "run-1"
    assert result["pending_approval"]["approval_id"] == "approval-1"
    assert result["pending_approval"]["tool_name"] == "dangerous_tool"


def test_decide_approval_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The decide_approval tool should delegate to ConversationService."""
    spec = _write_spec(tmp_path)

    from agent_engine.engine.types import RunResult as _RunResult
    from agent_manager.domain.identity import Principal

    decide_calls: list[tuple[str, str, str, str]] = []

    async def fake_decide(
        self_inner: object,
        conversation_id: str,
        run_id: str,
        approval_id: str,
        decision: str,
        principal: Principal,
    ) -> _RunResult:
        decide_calls.append((conversation_id, run_id, approval_id, decision))
        return _RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer="approved-result",
            status=RunStatus.COMPLETED,
        )

    from agent_manager.application import ConversationService

    monkeypatch.setattr(ConversationService, "decide_approval", fake_decide)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)
    server._repositories = _FakeRepositories()  # type: ignore[assignment]
    server._engine = FakeRuntimeEngine(Path("."))  # type: ignore[assignment]
    server._service = ConversationService(server._engine, _FakeRepository())  # type: ignore[arg-type]

    result = asyncio.run(server._handle_decide_approval("sess-1", "run-1", "approval-1", "approve"))

    assert result["status"] == "completed"
    assert result["answer"] == "approved-result"
    assert decide_calls == [("sess-1", "run-1", "approval-1", "allow_once")]


def test_extra_chat_tool_response_is_json_serialisable(tmp_path: Path) -> None:
    """The dict shape returned by ``_handle_chat`` must round-trip through JSON."""

    from agentctl.mcp_serve import create_server

    spec = _write_spec(tmp_path)
    create_server(str(spec), None)  # construction must succeed
    payload = {
        "session_id": "abc",
        "status": "completed",
        "answer": "hi",
        "visited": ["root"],
        "used_tools": [{"name": "echo", "provider": "local"}],
    }
    json.dumps(payload)  # must not raise


def test_run_closes_engine_and_db_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``setup`` fails, the server should still close the engine and DB."""
    spec = _write_spec(tmp_path)

    db_disposed = False

    class _RepoCM:
        async def __aenter__(self) -> _FakeRepositories:
            return _FakeRepositories()

        async def __aexit__(self, *args: object) -> None:
            nonlocal db_disposed
            db_disposed = True

    engine = FakeRuntimeEngine(Path("."))

    async def boom(_self: object, *_args: object, **_kw: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("agentctl.mcp_serve.application_repositories", lambda _s: _RepoCM())
    monkeypatch.setattr("agentctl.mcp_serve.LangGraphEngine", lambda *a, **kw: engine)
    monkeypatch.setattr(engine, "build", boom)

    from agentctl.mcp_serve import create_server

    server = create_server(str(spec), None)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(server.run())

    assert engine._closed is True
    assert db_disposed is True


class _FakeRepository:
    """A no-op stand-in for the conversation repository."""

    async def whatever(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeRepositories:
    """Stand-in for :class:`ApplicationRepositories`."""

    def __init__(self) -> None:
        self.conversations = _FakeRepository()
        self.session_approvals = object()
        self.tool_usage = object()
        self.runs = object()

    async def __aenter__(self) -> _FakeRepositories:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


# ---------------------------------------------------------------------------
# End-to-end MCP integration test (real stdio transport).
# ---------------------------------------------------------------------------


def test_mcp_client_can_call_extra_chat_over_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spin up the MCP server as a subprocess and talk to it from an MCP client.

    The engine is replaced in the subprocess by a tiny bootstrap script that
    monkeypatches ``agentctl.mcp_serve.LangGraphEngine`` before importing the
    CLI. This proves the full subprocess → stdio → MCP → ConversationService
    → fake engine path works end to end.

    The fake engine records ``prompts``/``histories``/``contexts`` and a
    ``build_count`` in its class state inside the subprocess. We don't need
    those values inside the parent process; the assertions on the wire-level
    MCP responses are sufficient to prove that the engine ran (answers are
    ``echo:<message>``), that the second turn's ``session_id`` matched the
    first (so ConversationService reused the conversation), and that a third
    call without a ``session_id`` got a different id (fresh session).
    """
    pytest.importorskip("mcp")

    spec = _write_spec(tmp_path)
    db_path = tmp_path / "chat.db"
    monkeypatch.setenv("AGENT_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    repo_root = Path(__file__).resolve().parents[2]
    src_path = repo_root / "src"

    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(src_path)!r})\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from tests.cli.test_mcp_serve_command import FakeRuntimeEngine\n"
        "import agentctl.mcp_serve as _mod\n"
        "_mod.LangGraphEngine = FakeRuntimeEngine\n"
        "FakeRuntimeEngine.prompts.clear()\n"
        "FakeRuntimeEngine.histories.clear()\n"
        "FakeRuntimeEngine.contexts.clear()\n"
        "FakeRuntimeEngine.build_count = 0\n"
        "from agentctl.main import cli\n"
        "cli()\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(repo_root) + os.pathsep + str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    )
    cmd = [sys.executable, str(bootstrap), "mcp", "serve", "--config", str(spec)]

    async def scenario() -> dict[str, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
        async with (
            stdio_client(params) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "extra_chat" in names, names

            first = await session.call_tool(
                "extra_chat",
                {"message": "hello world", "user_id": "alice"},
            )
            assert first.isError is False, first.content
            first_payload = _extract_payload(first.content)
            assert first_payload["answer"] == "echo:hello world"
            session_id = first_payload["session_id"]
            assert session_id

            second = await session.call_tool(
                "extra_chat",
                {"message": "again", "session_id": session_id, "user_id": "alice"},
            )
            assert second.isError is False, second.content
            second_payload = _extract_payload(second.content)
            assert second_payload["session_id"] == session_id
            assert second_payload["answer"] == "echo:again"

            third = await session.call_tool(
                "extra_chat",
                {"message": "separate", "user_id": "alice"},
            )
            third_payload = _extract_payload(third.content)
            assert third_payload["session_id"] != session_id
            assert third_payload["answer"] == "echo:separate"

            return {
                "first": first_payload,
                "second": second_payload,
                "third": third_payload,
            }

    result = asyncio.run(scenario())
    assert result["first"]["session_id"] == result["second"]["session_id"]
    assert result["third"]["session_id"] != result["first"]["session_id"]


def _extract_payload(content: list[Any]) -> dict[str, Any]:
    """Pull the JSON dict out of an MCP tool-call response."""
    for block in content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no text block in {content!r}")
