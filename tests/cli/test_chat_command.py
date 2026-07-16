"""Tests for the interactive ``agentctl chat`` console (local + remote modes).

No real LLM, MCP, or network is touched: the local engine is a fake stand-in and
the remote server is an httpx ``MockTransport``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from click.testing import CliRunner

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.engine.engine import Engine
from agent_engine.engine.types import PendingApproval, RunResult
from agent_engine.runtime.hooks.models import RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agentctl import chat as chat_mod
from agentctl.chat import run_chat_loop, run_remote_chat
from agentctl.main import cli

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

    async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
        self.questions.append(message)
        self.contexts.append(context)
        return RunResult(system_name="fake", visited=["a"], answer=f"echo:{message}")

    async def stream(
        self, message: str, *, context: RunContext | None = None
    ) -> AsyncIterator[RunStreamEvent]:
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

    async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
        self.questions.append(message)
        self.contexts.append(context)
        return next(self.results)

    async def stream(
        self, message: str, *, context: RunContext | None = None
    ) -> AsyncIterator[RunStreamEvent]:
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

    async def resume(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
    ) -> RunResult:
        assert isinstance(decision, ApprovalDecision)
        self.resume_calls.append((run_id, approval_id, decision, caller_user_id))
        return next(self.results)


class CollectingEcho:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, message: str = "", *, err: bool = False) -> None:
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
        engine, stream=False, read_line=scripted_reader(["first", "second"]), echo=echo
    )
    assert engine.questions == ["first", "second"]
    answers = [line for line in echo.lines if line.startswith("Agent > ")]
    assert answers == ["Agent > echo:first", "Agent > echo:second"]


async def test_local_loop_streaming_uses_stream_path() -> None:
    engine = FakeEngine()
    echo = CollectingEcho()
    await run_chat_loop(engine, stream=True, read_line=scripted_reader(["hi"]), echo=echo)
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
        echo=CollectingEcho(),
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
        echo=CollectingEcho(),
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
        echo=CollectingEcho(),
    )
    sessions = {c.conversation_id for c in engine.contexts if c is not None}
    assert len(sessions) == 1  # one auto id, shared by every question
    assert next(iter(sessions))  # non-empty


async def test_local_one_failure_does_not_kill_loop() -> None:
    class FlakyEngine(FakeEngine):
        async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
            if message == "boom":
                raise RuntimeError("model exploded")
            return await super().run(message)

    engine = FlakyEngine()
    echo = CollectingEcho()
    await run_chat_loop(engine, stream=False, read_line=scripted_reader(["boom", "ok"]), echo=echo)
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
                status="pending_approval",
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
        echo=echo,
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
                status="pending_approval",
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
        echo=echo,
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
                status="pending_approval",
                pending_approval=pending,
            ),
        ]
    )
    echo = CollectingEcho()

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question"]),
        echo=echo,
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
                status="pending_approval",
                pending_approval=pending,
            ),
        ]
    )

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question", word]),
        echo=CollectingEcho(),
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
                status="pending_approval",
                pending_approval=first,
            ),
            RunResult(
                system_name="fake",
                visited=[],
                answer="",
                status="pending_approval",
                pending_approval=second,
            ),
            RunResult(system_name="fake", visited=[], answer="complete"),
        ]
    )

    await run_chat_loop(
        engine,
        stream=False,
        read_line=scripted_reader(["question", "o", "d"]),
        echo=CollectingEcho(),
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
        echo=echo,
    )

    assert engine.resume_calls[0][2] is ApprovalDecision.ALLOW_ONCE
    assert "Agent > stream resumed" in echo.lines


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
            echo=echo,
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
            echo=echo,
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
            echo=echo,
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
            echo=CollectingEcho(),
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
            echo=echo,
            client=client,
        )
    assert any("request failed" in line for line in echo.lines)
