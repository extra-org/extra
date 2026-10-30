"""Tests for the interactive ``agentctl chat`` console (local + remote modes).

No real LLM, MCP, or network is touched: local tests use fake engines or the
real engine with fake models, and remote tests use httpx ``MockTransport``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.models import RunStatus
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.types import ChatMessage, PendingApproval, RunResult
from agent_engine.runtime.hooks.models import AuthContext, RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agentctl import chat as chat_mod
from agentctl.chat import run_chat_loop, run_remote_chat
from agentctl.main import cli
from tests.approvals.test_engine_hitl import _multi_tool_spec
from tests.approvals.test_resume_binding import write_tool
from tests.engine.test_engine_flow import orchestrator, system
from tests.fixtures.utils import fake_model_factory

# ---------------------------------------------------------------------------
# fakes / helpers
# ---------------------------------------------------------------------------


def scripted_reader(inputs: list[str]) -> Callable[[str], str]:
    """A ``read_line`` that returns each scripted line, then raises EOFError."""
    it = iter(inputs)

    def _read(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return _read


class FakeEngine(Engine):
    """Records every question, build count, and the context of each call."""

    def __init__(self) -> None:
        self.builds = 0
        self.questions: list[str] = []
        self.contexts: list[RunContext | None] = []

    async def build(self, _spec: object) -> None:
        self.builds += 1

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        del history
        self.questions.append(message)
        self.contexts.append(context)
        return RunResult(system_name="fake", visited=["a"], answer=f"echo:{message}")

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        del history
        self.questions.append(message)
        self.contexts.append(context)
        for chunk in ("a", "b"):
            yield RunStreamEvent(type="answer_delta", content=chunk)


def pending_approval(
    *, run_id: str = "run-1", approval_id: str = "approval-1", tool_name: str = "read_docs"
) -> PendingApproval:
    return PendingApproval(
        run_id=run_id,
        approval_id=approval_id,
        agent_id="researcher",
        tool_name=tool_name,
        description=f"Researcher wants to call {tool_name}; it has not been executed.",
        provider="mcp",
        server_id="deepwiki",
        arguments={"repoName": "extra-org/extra"},
    )


class ApprovalFakeEngine(FakeEngine):
    def __init__(self, results: list[RunResult], *, stream_pending: PendingApproval | None = None):
        super().__init__()
        self.results = iter(results)
        self.stream_pending = stream_pending
        self.resume_calls: list[tuple[str, str, ApprovalDecision, str | None]] = []

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        del history
        self.questions.append(message)
        self.contexts.append(context)
        return next(self.results)

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        del history
        self.questions.append(message)
        self.contexts.append(context)
        assert self.stream_pending is not None
        yield RunStreamEvent(
            type="pending_approval",
            run_id=self.stream_pending.run_id,
            approval_id=self.stream_pending.approval_id,
            agent_id=self.stream_pending.agent_id,
            tool_name=self.stream_pending.tool_name,
            description=self.stream_pending.description,
        )

    async def get_pending_approval(self, run_id: str) -> PendingApproval | None:
        assert self.stream_pending is not None
        assert run_id == self.stream_pending.run_id
        return self.stream_pending

    async def get_processed_result(
        self,
        run_id: str,
        approval_id: str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
    ) -> RunResult | None:
        del run_id, approval_id, caller_user_id, caller_session_id
        return None

    async def resume(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
        caller_session_id: str | None = None,
        access_token: str | None = None,
        context: RunContext | None = None,
    ) -> RunResult:
        del caller_session_id, access_token, context
        assert isinstance(decision, ApprovalDecision)
        self.resume_calls.append((run_id, approval_id, decision, caller_user_id))
        return next(self.results)


class CollectingEcho:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str = "", *, err: bool = False) -> None:
        self.lines.append(message)


# ---------------------------------------------------------------------------
# CLI validation: exactly one of --config / --url
# ---------------------------------------------------------------------------


def test_chat_config_only_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_local(
        config: str, env: str | None, stream: bool, session_id: str | None = None
    ) -> None:
        seen["config"] = config
        seen["session_id"] = session_id

    monkeypatch.setattr("agentctl.chat.run_local_chat", fake_local)
    res = CliRunner().invoke(cli, ["chat", "--config", "x.yml"])
    assert res.exit_code == 0, res.output
    assert seen["config"] == "x.yml"


def test_chat_url_only_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_remote(url: str, stream: bool, session_id: str | None = None) -> None:
        seen["url"] = url
        seen["session_id"] = session_id

    monkeypatch.setattr("agentctl.chat.run_remote_chat", fake_remote)
    res = CliRunner().invoke(cli, ["chat", "--url", "http://localhost:8090"])
    assert res.exit_code == 0, res.output
    assert seen["url"] == "http://localhost:8090"


def test_chat_both_config_and_url_fails() -> None:
    res = CliRunner().invoke(cli, ["chat", "--config", "x.yml", "--url", "http://h"])
    assert res.exit_code != 0
    assert "not both" in res.output


def test_chat_neither_config_nor_url_fails() -> None:
    res = CliRunner().invoke(cli, ["chat"])
    assert res.exit_code != 0
    assert "Pass one of --config" in res.output


# ---------------------------------------------------------------------------
# console loop: exit words, empty input, EOF, Ctrl-C
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["exit", "quit", "q", "EXIT", "Quit"])
def test_loop_exits_on_exit_words(word: str) -> None:
    questions = list(chat_mod._iter_questions(scripted_reader([word, "never asked"])))
    assert questions == []


def test_loop_ignores_empty_input() -> None:
    questions = list(chat_mod._iter_questions(scripted_reader(["", "  ", "hello", ""])))
    assert questions == ["hello"]


def test_loop_stops_cleanly_on_eof() -> None:
    # scripted_reader raises EOFError once inputs are exhausted.
    questions = list(chat_mod._iter_questions(scripted_reader(["one"])))
    assert questions == ["one"]


def test_loop_stops_cleanly_on_keyboard_interrupt() -> None:
    def _read(_prompt: str) -> str:
        raise KeyboardInterrupt

    assert list(chat_mod._iter_questions(_read)) == []


# ---------------------------------------------------------------------------
# local mode: engine reused, each question independent
# ---------------------------------------------------------------------------


async def test_local_loop_reuses_engine_and_sends_each_question() -> None:
    engine = FakeEngine()
    echo = CollectingEcho()
    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["first", "second"]),
        echo=echo.write,
    )
    assert engine.questions == ["first", "second"]
    answers = [line for line in echo.lines if line.startswith("Agent > ")]
    assert answers == ["Agent > echo:first", "Agent > echo:second"]


async def test_local_loop_streaming_uses_stream_path() -> None:
    engine = FakeEngine()
    echo = CollectingEcho()
    await run_chat_loop(engine, stream=True, read_line=scripted_reader(["hi"]), echo=echo.write)
    assert engine.questions == ["hi"]


async def test_local_mode_builds_engine_once(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()

    class _CtxEngine:
        def __init__(self, _base_dir: object, **_kwargs: object) -> None: ...

        async def __aenter__(self) -> FakeEngine:
            return engine

        async def __aexit__(self, *args: object) -> None: ...

    monkeypatch.setattr("agent_engine.engine.langgraph.engine.LangGraphEngine", _CtxEngine)
    monkeypatch.setattr(chat_mod, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod, "load_and_validate", lambda config: (object(), "/base"))

    await chat_mod.run_local_chat(
        "spec.yml",
        None,
        stream=False,
        read_line=scripted_reader(["a", "b", "c"]),
        echo=CollectingEcho().write,
    )
    assert engine.builds == 1
    assert engine.questions == ["a", "b", "c"]


def _patch_local_engine(monkeypatch: pytest.MonkeyPatch, engine: FakeEngine) -> None:
    class _CtxEngine:
        def __init__(self, _base_dir: object, **_kwargs: object) -> None: ...

        async def __aenter__(self) -> FakeEngine:
            return engine

        async def __aexit__(self, *args: object) -> None: ...

    monkeypatch.setattr("agent_engine.engine.langgraph.engine.LangGraphEngine", _CtxEngine)
    monkeypatch.setattr(chat_mod, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod, "load_and_validate", lambda config: (object(), "/base"))


async def test_local_chat_groups_questions_under_one_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    _patch_local_engine(monkeypatch, engine)

    await chat_mod.run_local_chat(
        "spec.yml",
        None,
        stream=False,
        session_id="sess-xyz",
        read_line=scripted_reader(["a", "b", "c"]),
        echo=CollectingEcho().write,
    )
    sessions = {c.conversation_id for c in engine.contexts if c is not None}
    assert sessions == {"sess-xyz"}
    assert len(engine.contexts) == 3


async def test_local_chat_autogenerates_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    _patch_local_engine(monkeypatch, engine)

    await chat_mod.run_local_chat(
        "spec.yml",
        None,
        stream=False,
        read_line=scripted_reader(["a", "b"]),
        echo=CollectingEcho().write,
    )
    sessions = {c.conversation_id for c in engine.contexts if c is not None}
    assert len(sessions) == 1  # one auto id, shared by every question
    assert next(iter(sessions))  # non-empty


async def test_local_one_failure_does_not_kill_loop() -> None:
    class FlakyEngine(FakeEngine):
        async def run(
            self,
            message: str,
            *,
            history: Sequence[ChatMessage] = (),
            context: RunContext | None = None,
        ) -> RunResult:
            if message == "boom":
                raise RuntimeError("model exploded")
            return await super().run(message, history=history, context=context)

    engine = FlakyEngine()
    echo = CollectingEcho()
    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["boom", "ok"]),
        echo=echo.write,
    )
    assert any("model exploded" in line for line in echo.lines)
    assert any(line == "Agent > echo:ok" for line in echo.lines)


# ---------------------------------------------------------------------------
# local mode: human approval and resume
# ---------------------------------------------------------------------------


async def test_local_chat_prompts_for_approval_and_resumes_allow_once() -> None:
    pending = pending_approval()
    engine = ApprovalFakeEngine(
        [
            RunResult(
                system_name="fake",
                visited=["researcher"],
                answer="",
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=pending,
            ),
            RunResult(system_name="fake", visited=["researcher"], answer="architecture"),
        ]
    )
    echo = CollectingEcho()

    await run_chat_loop(
        engine,
        stream=False,
        context=RunContext(conversation_id="session-1", user_id="user-1"),
        read_line=scripted_reader(["question", "o"]),
        echo=echo.write,
    )

    assert engine.resume_calls == [("run-1", "approval-1", ApprovalDecision.ALLOW_ONCE, "user-1")]
    assert "  Tool      : deepwiki.read_docs (mcp)" in echo.lines
    assert '  Arguments : {"repoName": "extra-org/extra"}' in echo.lines
    assert "Agent > architecture" in echo.lines


async def test_local_chat_reprompts_invalid_approval_then_allows_session() -> None:
    pending = pending_approval()
    engine = ApprovalFakeEngine(
        [
            RunResult(
                system_name="fake",
                visited=[],
                answer="",
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=pending,
            ),
            RunResult(system_name="fake", visited=[], answer="done"),
        ]
    )
    echo = CollectingEcho()

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question", "maybe", "s"]),
        echo=echo.write,
    )

    assert engine.resume_calls[0][2] is ApprovalDecision.ALLOW_FOR_SESSION
    assert any("Enter 'o'" in line for line in echo.lines)


async def test_local_chat_stops_when_approval_input_ends() -> None:
    pending = pending_approval()
    engine = ApprovalFakeEngine(
        [
            RunResult(
                system_name="fake",
                visited=[],
                answer="",
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=pending,
            ),
        ]
    )
    echo = CollectingEcho()

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question"]),
        echo=echo.write,
    )

    assert engine.resume_calls == []
    assert not any(line.startswith("Agent > ") for line in echo.lines)


@pytest.mark.parametrize("word", ["exit", "quit", "q", "EXIT", "Quit"])
async def test_local_chat_exit_words_stop_from_approval_prompt(word: str) -> None:
    pending = pending_approval()
    engine = ApprovalFakeEngine(
        [
            RunResult(
                system_name="fake",
                visited=[],
                answer="",
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=pending,
            ),
        ]
    )

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question", word]),
        echo=CollectingEcho().write,
    )

    assert engine.resume_calls == []


async def test_local_chat_handles_multiple_approvals_in_one_run() -> None:
    first = pending_approval()
    second = pending_approval(approval_id="approval-2", tool_name="read_page")
    engine = ApprovalFakeEngine(
        [
            RunResult(
                system_name="fake",
                visited=[],
                answer="",
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=first,
            ),
            RunResult(
                system_name="fake",
                visited=[],
                answer="",
                status=RunStatus.PENDING_APPROVAL,
                pending_approval=second,
            ),
            RunResult(system_name="fake", visited=[], answer="complete"),
        ]
    )

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question", "o", "d"]),
        echo=CollectingEcho().write,
    )

    assert [call[2] for call in engine.resume_calls] == [
        ApprovalDecision.ALLOW_ONCE,
        ApprovalDecision.DENY,
    ]


async def test_local_stream_prompts_and_resumes_pending_approval() -> None:
    pending = pending_approval()
    engine = ApprovalFakeEngine(
        [RunResult(system_name="fake", visited=[], answer="stream resumed")],
        stream_pending=pending,
    )
    echo = CollectingEcho()

    await run_chat_loop(
        engine,
        stream=True,
        read_line=scripted_reader(["question", "allow once"]),
        echo=echo.write,
    )

    assert engine.resume_calls[0][2] is ApprovalDecision.ALLOW_ONCE
    assert "Agent > stream resumed" in echo.lines


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("root_kind", ["agent", "orchestrator"])
@pytest.mark.parametrize("revoke", [False, True])
async def test_local_approval_retains_current_authorization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stream: bool, root_kind: str, revoke: bool
) -> None:
    output = tmp_path / "executed.log"
    policy = tmp_path / "policy.jsonl"
    write_tool(tmp_path, "send_email", output)
    (tmp_path / "plugins" / "access.py").write_text(
        "import json\n"
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        f"        with open({str(policy)!r}, 'a') as output:\n"
        "            output.write(json.dumps(ctx) + '\\n')\n"
        "        auth = ctx.get('auth', {})\n"
        "        return ('admin' in auth.get('roles', ())\n"
        "                and 'write' in auth.get('scopes', ())\n"
        "                and ctx.get('metadata', {}).get('permission') is True)\n",
        encoding="utf-8",
    )
    spec = _multi_tool_spec("send_email")
    if root_kind == "orchestrator":
        spec = system(orchestrator("root", [spec.graph]))
    spec = replace(spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True)))
    context = RunContext(
        run_id="local-run",
        user_id="owner",
        conversation_id="local-session",
        metadata={"permission": True},
        auth_context=AuthContext(
            user_id="owner",
            roles=("admin",),
            scopes=("write",),
            inbound_access_token="local-test-credential",
        ),
    )
    inputs = scripted_reader(["hello", "o"])

    def read_line(prompt: str) -> str:
        if prompt.startswith("Allow?") and revoke:
            # Metadata is the explicitly mutable host/hook enrichment field.
            context.metadata["permission"] = False
        return inputs(prompt)

    echo = CollectingEcho()
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        await run_chat_loop(
            engine, stream=stream, context=context, read_line=read_line, echo=echo.write
        )
        if revoke:
            assert not output.exists()
            assert await engine.get_run_status("local-run") == RunStatus.FAILED
            assert await engine.get_pending_approval("local-run") is None
        else:
            assert not any(line.startswith("✗") for line in echo.lines), echo.lines
            assert len(output.read_text().splitlines()) == 1
            assert await engine.get_run_status("local-run") == RunStatus.COMPLETED
            assert any(line.startswith("Agent > ") for line in echo.lines)
    decisions = policy.read_text()
    latest = json.loads(decisions.splitlines()[-1])
    assert latest["metadata"]["permission"] is (not revoke)
    assert latest["auth"]["roles"] == ["admin"]
    assert latest["auth"]["scopes"] == ["write"]
    assert latest["user_id"] == "owner"
    assert latest["conversation_id"] == "local-session"
    assert "local-test-credential" not in decisions
    captured = capsys.readouterr()
    assert "local-test-credential" not in "\n".join(echo.lines) + captured.out + captured.err


# ---------------------------------------------------------------------------
# remote mode: endpoint, error handling, stream flag
# ---------------------------------------------------------------------------


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_remote_sends_to_invoke_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        body = json.loads(request.content)
        return httpx.Response(200, json={"answer": f"server:{body['message']}"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv:8090",
            stream=False,
            read_line=scripted_reader(["question one"]),
            echo=echo.write,
            client=client,
        )
    assert seen == ["/invoke"]


async def test_remote_chat_sends_session_header() -> None:
    seen_sessions: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_sessions.append(request.headers.get("x-session-id"))
        return httpx.Response(200, json={"answer": "ok"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            session_id="rsess",
            read_line=scripted_reader(["one", "two"]),
            echo=echo.write,
            client=client,
        )
    assert seen_sessions == ["rsess", "rsess"]  # same session on every request
    assert echo.lines.count("Agent > ok") == 2


async def test_remote_server_error_does_not_kill_loop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["message"] == "bad":
            return httpx.Response(500, json={"detail": "boom on server"})
        return httpx.Response(200, json={"answer": "fine"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["bad", "good"]),
            echo=echo.write,
            client=client,
        )
    assert any("server error 500" in line and "boom on server" in line for line in echo.lines)
    assert "Agent > fine" in echo.lines


async def test_remote_stream_flag_uses_stream_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        sse = 'data: {"type": "answer_delta", "content": "hello"}\n\n'
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=True,
            read_line=scripted_reader(["hi"]),
            echo=CollectingEcho().write,
            client=client,
        )
    assert seen == ["/stream"]


async def test_remote_network_error_does_not_kill_loop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["x"]),
            echo=echo.write,
            client=client,
        )
    assert any("request failed" in line for line in echo.lines)


# ---------------------------------------------------------------------------
# remote mode: human approval and resume
# ---------------------------------------------------------------------------


def _pending_json(pending: PendingApproval) -> dict[str, object]:
    return {
        "run_id": pending.run_id,
        "approval_id": pending.approval_id,
        "agent_id": pending.agent_id,
        "tool_name": pending.tool_name,
        "description": pending.description,
        "provider": pending.provider,
        "server_id": pending.server_id,
        "arguments": pending.arguments,
    }


async def test_remote_chat_prompts_for_approval_and_resumes_allow_once() -> None:
    pending = pending_approval()
    decisions_sent: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/invoke":
            return httpx.Response(
                200,
                json={
                    "answer": "",
                    "status": "pending_approval",
                    "pending_approval": _pending_json(pending),
                },
            )
        decisions_sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"answer": "architecture", "status": "completed"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["question", "o"]),
            echo=echo.write,
            client=client,
        )

    assert decisions_sent == [
        (
            f"/runs/{pending.run_id}/approvals/{pending.approval_id}/decision",
            {"decision": "allow_once"},
        )
    ]
    assert "  Tool      : deepwiki.read_docs (mcp)" in echo.lines
    assert "Agent > architecture" in echo.lines


async def test_remote_chat_resumes_with_allow_for_session() -> None:
    pending = pending_approval()
    decisions_sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/invoke":
            return httpx.Response(
                200,
                json={
                    "answer": "",
                    "status": "pending_approval",
                    "pending_approval": _pending_json(pending),
                },
            )
        decisions_sent.append(json.loads(request.content))
        return httpx.Response(200, json={"answer": "done", "status": "completed"})

    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["question", "s"]),
            echo=CollectingEcho().write,
            client=client,
        )

    assert decisions_sent == [{"decision": "allow_for_session"}]


async def test_remote_chat_resumes_with_deny() -> None:
    pending = pending_approval()
    decisions_sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/invoke":
            return httpx.Response(
                200,
                json={
                    "answer": "",
                    "status": "pending_approval",
                    "pending_approval": _pending_json(pending),
                },
            )
        decisions_sent.append(json.loads(request.content))
        return httpx.Response(200, json={"answer": "denied", "status": "completed"})

    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["question", "d"]),
            echo=CollectingEcho().write,
            client=client,
        )

    assert decisions_sent == [{"decision": "deny"}]


async def test_remote_chat_handles_multiple_approvals_in_one_run() -> None:
    first = pending_approval()
    second = pending_approval(approval_id="approval-2", tool_name="read_page")
    responses = iter(
        [
            {"answer": "", "status": "pending_approval", "pending_approval": _pending_json(first)},
            {"answer": "", "status": "pending_approval", "pending_approval": _pending_json(second)},
            {"answer": "complete", "status": "completed"},
        ]
    )
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=next(responses))

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            # Only "question" is a real user message; "o"/"d" must be consumed
            # as approval-prompt answers, not sent as separate /invoke calls.
            read_line=scripted_reader(["question", "o", "d"]),
            echo=echo.write,
            client=client,
        )

    assert [path for path, _ in seen] == [
        "/invoke",
        f"/runs/{first.run_id}/approvals/{first.approval_id}/decision",
        f"/runs/{second.run_id}/approvals/{second.approval_id}/decision",
    ]
    assert [body["decision"] for _, body in seen[1:]] == ["allow_once", "deny"]
    assert "Agent > complete" in echo.lines


async def test_remote_chat_stops_when_approval_input_ends() -> None:
    pending = pending_approval()
    decision_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal decision_calls
        if request.url.path == "/invoke":
            return httpx.Response(
                200,
                json={
                    "answer": "",
                    "status": "pending_approval",
                    "pending_approval": _pending_json(pending),
                },
            )
        decision_calls += 1
        return httpx.Response(200, json={"answer": "should not get here", "status": "completed"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["question"]),  # EOF right at the approval prompt
            echo=echo.write,
            client=client,
        )

    assert decision_calls == 0
    assert not any(line.startswith("Agent > ") for line in echo.lines)


async def test_remote_stream_prompts_and_resumes_pending_approval() -> None:
    pending = pending_approval()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stream":
            payload = {"type": "pending_approval", **_pending_json(pending)}
            sse = f"data: {json.dumps(payload)}\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"answer": "stream resumed", "status": "completed"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=True,
            read_line=scripted_reader(["question", "allow once"]),
            echo=echo.write,
            client=client,
        )

    assert "Agent > stream resumed" in echo.lines


async def test_remote_decision_error_does_not_kill_loop() -> None:
    pending = pending_approval()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/invoke":
            body = json.loads(request.content)
            if body["message"] == "first":
                return httpx.Response(
                    200,
                    json={
                        "answer": "",
                        "status": "pending_approval",
                        "pending_approval": _pending_json(pending),
                    },
                )
            return httpx.Response(200, json={"answer": "second works", "status": "completed"})
        return httpx.Response(404, json={"detail": "run not found"})

    echo = CollectingEcho()
    async with _mock_client(handler) as client:
        await run_remote_chat(
            "http://srv",
            stream=False,
            read_line=scripted_reader(["first", "o", "second"]),
            echo=echo.write,
            client=client,
        )

    assert any("server error 404" in line and "run not found" in line for line in echo.lines)
    assert "Agent > second works" in echo.lines
