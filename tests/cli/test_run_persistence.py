from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from click.testing import CliRunner, Result

from agent_engine.engine.types import RunResult
from agent_engine.runtime.hooks import RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from agent_manager.infrastructure.persistence.tables import ConversationSnapshotRow
from agentctl import main as main_mod
from agentctl.main import cli


class FakeRuntimeEngine:
    prompts: ClassVar[list[str]] = []
    contexts: ClassVar[list[RunContext | None]] = []

    def __init__(self, _base_dir: Path) -> None: ...

    async def __aenter__(self) -> FakeRuntimeEngine:
        return self

    async def __aexit__(self, *args: object) -> None: ...

    async def build(self, _spec: object) -> None: ...

    async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
        self.prompts.append(message)
        self.contexts.append(context)
        return RunResult(
            system_name="Fake System",
            visited=["fake_agent"],
            answer=f"answer-{len(self.prompts)}",
        )

    async def stream(
        self, message: str, *, context: RunContext | None = None
    ) -> AsyncIterator[RunStreamEvent]:
        self.prompts.append(message)
        self.contexts.append(context)
        yield RunStreamEvent(type="answer_delta", content="chunk")
        yield RunStreamEvent(
            type="final",
            content=f"answer-{len(self.prompts)}",
            route=("fake_agent",),
            system_name="Fake System",
        )


class FailingRuntimeEngine(FakeRuntimeEngine):
    async def run(self, message: str, *, context: RunContext | None = None) -> RunResult:
        self.prompts.append(message)
        self.contexts.append(context)
        raise RuntimeError("model failed safely")


def _write_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "agents.yml"
    spec.write_text(
        "system: {name: Fake System}\n"
        "agents: {fake_agent: {description: fake}}\n"
        "graph: {fake_agent: null}\n",
        encoding="utf-8",
    )
    return spec


async def _repo(db_url: str) -> tuple[SqlRepository, Any]:
    engine = create_db_engine(db_url)
    return SqlRepository(session_factory(engine)), engine


async def _messages_and_snapshot(db_url: str, session_id: str) -> tuple[list[Any], Any]:
    repo, engine = await _repo(db_url)
    try:
        return (
            await repo.list_conversation_messages(session_id),
            await repo.get_snapshot(session_id),
        )
    finally:
        await engine.dispose()


async def _session_and_messages(db_url: str, session_id: str) -> tuple[Any, list[Any]]:
    repo, engine = await _repo(db_url)
    try:
        return await repo.get_session(session_id), await repo.list_conversation_messages(session_id)
    finally:
        await engine.dispose()


async def _messages_snapshot_and_user(db_url: str, session_id: str) -> tuple[list[Any], Any, Any]:
    repo, engine = await _repo(db_url)
    try:
        return (
            await repo.list_conversation_messages(session_id),
            await repo.get_snapshot(session_id),
            await repo.get_user("local-user"),
        )
    finally:
        await engine.dispose()


async def _delete_snapshot_and_rebuild(db_url: str, session_id: str) -> tuple[Any, Any]:
    repo, engine = await _repo(db_url)
    try:
        async with session_factory(engine)() as session, session.begin():
            row = await session.get(ConversationSnapshotRow, session_id)
            assert row is not None
            await session.delete(row)
        assert await repo.get_snapshot(session_id) is None
        rebuilt = await repo.get_context(session_id)
        snapshot = await repo.get_snapshot(session_id)
        return rebuilt, snapshot
    finally:
        await engine.dispose()


def _run_cli(spec: Path, message: str, *extra: str) -> Result:
    return CliRunner().invoke(
        cli,
        [
            "--log-level",
            "WARNING",
            "run",
            "--config",
            str(spec),
            "--message",
            message,
            *extra,
        ],
    )


def test_agentctl_run_persists_messages_and_reuses_session_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _write_spec(tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}"
    monkeypatch.setenv("AGENT_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_DB_URL", db_url)
    monkeypatch.setattr(main_mod, "LangGraphEngine", FakeRuntimeEngine)
    FakeRuntimeEngine.prompts.clear()
    FakeRuntimeEngine.contexts.clear()

    first = _run_cli(spec, "hello", "--session-id", "sess-1", "--user-id", "u1")
    assert first.exit_code == 0, first.output
    messages, snapshot = asyncio.run(_messages_and_snapshot(db_url, "sess-1"))
    assert [(m.role.value, m.content) for m in messages] == [
        ("user", "hello"),
        ("assistant", "answer-1"),
    ]
    assert snapshot is not None
    assert snapshot.message_count == 2

    second = _run_cli(spec, "what did I say?", "--session-id", "sess-1", "--user-id", "u1")
    assert second.exit_code == 0, second.output

    messages, snapshot = asyncio.run(_messages_and_snapshot(db_url, "sess-1"))
    assert [(m.role.value, m.content) for m in messages] == [
        ("user", "hello"),
        ("assistant", "answer-1"),
        ("user", "what did I say?"),
        ("assistant", "answer-2"),
    ]
    assert snapshot is not None
    assert snapshot.message_count == 4

    assert "Conversation so far:" in FakeRuntimeEngine.prompts[1]
    assert "user: hello" in FakeRuntimeEngine.prompts[1]
    assert "assistant: answer-1" in FakeRuntimeEngine.prompts[1]
    assert FakeRuntimeEngine.prompts[1].rstrip().endswith("what did I say?")

    run_ids = [m.run_id for m in messages]
    assert run_ids[0] == run_ids[1]
    assert run_ids[2] == run_ids[3]
    assert run_ids[0] != run_ids[2]
    assert {m.session_id for m in messages} == {"sess-1"}
    assert {ctx.conversation_id for ctx in FakeRuntimeEngine.contexts if ctx} == {"sess-1"}

    rebuilt, snapshot = asyncio.run(_delete_snapshot_and_rebuild(db_url, "sess-1"))
    assert rebuilt.source == "rebuilt"
    assert [m.content for m in rebuilt.messages][-2:] == ["what did I say?", "answer-2"]
    assert snapshot is not None


def test_agentctl_run_without_session_prints_reusable_generated_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _write_spec(tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'generated.db'}"
    monkeypatch.setenv("AGENT_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_DB_URL", db_url)
    monkeypatch.setattr(main_mod, "LangGraphEngine", FakeRuntimeEngine)
    FakeRuntimeEngine.prompts.clear()
    FakeRuntimeEngine.contexts.clear()

    result = _run_cli(spec, "hello")

    assert result.exit_code == 0, result.output
    output = result.output + getattr(result, "stderr", "")
    match = re.search(r"session: ([A-Za-z0-9]+) \(generated; reuse with --session-id\)", output)
    assert match is not None, output
    session_id = match.group(1)

    session, messages = asyncio.run(_session_and_messages(db_url, session_id))
    assert session is not None
    assert session.user_id == "local-user"
    assert [(m.role.value, m.content) for m in messages] == [
        ("user", "hello"),
        ("assistant", "answer-1"),
    ]
    assert {ctx.user_id for ctx in FakeRuntimeEngine.contexts if ctx} == {"local-user"}


def test_agentctl_run_failure_keeps_user_message_without_assistant_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _write_spec(tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}"
    monkeypatch.setenv("AGENT_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_DB_URL", db_url)
    monkeypatch.setattr(main_mod, "LangGraphEngine", FailingRuntimeEngine)
    FailingRuntimeEngine.prompts.clear()
    FailingRuntimeEngine.contexts.clear()

    result = _run_cli(spec, "persist me before failure", "--session-id", "sess-fail")

    assert result.exit_code != 0
    messages, snapshot, user = asyncio.run(_messages_snapshot_and_user(db_url, "sess-fail"))
    assert user is not None
    assert [(m.role.value, m.content) for m in messages] == [
        ("user", "persist me before failure"),
    ]
    assert messages[0].user_id == "local-user"
    assert snapshot is not None
    assert snapshot.message_count == 1
    assert {ctx.conversation_id for ctx in FailingRuntimeEngine.contexts if ctx} == {"sess-fail"}
    assert {ctx.user_id for ctx in FailingRuntimeEngine.contexts if ctx} == {"local-user"}
