"""HTTP routes via TestClient — stub engine + in-memory repo, no DB or LLM."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_engine.engine.engine import Engine
from agent_engine.engine.types import ChatMessage, RunResult
from agent_engine.runtime.hooks.models import RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.api.routes import router
from agent_manager.application import ConversationService
from agent_manager.domain import ContextUsage, thread_title
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from tests.agent_manager.conftest import RecordingEngine


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.state.service = ConversationService(RecordingEngine(), MemoryRepository())
    app.include_router(router)
    return TestClient(app)


def test_create_send_history_round_trip(client: TestClient) -> None:
    cid = client.post("/conversations").json()["conversation_id"]

    sent = client.post(f"/conversations/{cid}/messages", json={"message": "hello"})
    assert sent.status_code == 200
    assert sent.json()["answer"] == "answer:hello"

    msgs = client.get(f"/conversations/{cid}/messages").json()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hello"),
        ("assistant", "answer:hello"),
    ]


def test_list_conversations_returns_titled_threads_scoped_to_user(client: TestClient) -> None:
    a = client.post("/conversations", json={"user_id": "u1"}).json()["conversation_id"]
    client.post(f"/conversations/{a}/messages", json={"message": "first thread"})
    b = client.post("/conversations", json={"user_id": "u1"}).json()["conversation_id"]
    client.post(f"/conversations/{b}/messages", json={"message": "second thread"})

    threads = client.get("/conversations", params={"user_id": "u1"}).json()
    assert {t["conversation_id"]: t["title"] for t in threads} == {
        a: "first thread",
        b: "second thread",
    }
    assert client.get("/conversations", params={"user_id": "u2"}).json() == []


def test_thread_title_collapses_whitespace_and_truncates() -> None:
    assert thread_title("  hi   there  ") == "hi there"
    assert thread_title("") == "New chat"
    truncated = thread_title("x" * 60)
    assert len(truncated) == 48 and truncated.endswith("…")


def test_unknown_conversation_returns_404(client: TestClient) -> None:
    assert client.get("/conversations/nope/messages").status_code == 404
    assert client.post("/conversations/nope/messages", json={"message": "x"}).status_code == 404
    assert client.get("/conversations/nope/usage").status_code == 404


def test_usage_reports_null_budget_when_unset(client: TestClient) -> None:
    cid = client.post("/conversations").json()["conversation_id"]
    assert client.get(f"/conversations/{cid}/usage").json() == {
        "used_tokens": 0,
        "max_tokens": None,
        "percent": 0.0,
        "severity": "normal",
    }


def test_usage_reports_accumulated_tokens_and_severity_against_budget() -> None:
    class TokenEngine(RecordingEngine):
        async def run(
            self,
            message: str,
            *,
            history: Sequence[ChatMessage] = (),
            context: RunContext | None = None,
        ) -> RunResult:
            return RunResult(
                system_name="stub",
                visited=["agent"],
                answer="ok",
                input_tokens=600,
                output_tokens=100,
            )

    app = FastAPI()
    app.state.service = ConversationService(
        TokenEngine(), MemoryRepository(), max_tokens=1000
    )
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    client.post(f"/conversations/{cid}/messages", json={"message": "hi"})

    body = client.get(f"/conversations/{cid}/usage").json()
    assert body["used_tokens"] == 700
    assert body["max_tokens"] == 1000
    assert body["percent"] == pytest.approx(70.0)
    assert body["severity"] == "warning"


def test_context_usage_severity_thresholds() -> None:
    assert ContextUsage.from_totals(0, None).severity == "normal"
    assert ContextUsage.from_totals(640, 1000).severity == "normal"
    assert ContextUsage.from_totals(650, 1000).severity == "warning"
    assert ContextUsage.from_totals(850, 1000).severity == "warning"
    assert ContextUsage.from_totals(851, 1000).severity == "critical"
    assert ContextUsage.from_totals(5000, 1000).percent == 100.0


class _SubAgentEngine(Engine):
    """Stub that mimics a parent orchestrator routing to a sub-agent.

    The route visits the root orchestrator and then a sub-agent path. Lets us
    assert the real conversation API surfaces sub-agent participation without an
    LLM.
    """

    async def build(self, _spec: object) -> None: ...

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        return RunResult(
            system_name="Knowledge Assistant",
            visited=["knowledge_router", "knowledge_router/documentation_agent"],
            answer="The available document tags are: finance, legal, hr.",
        )

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        yield RunStreamEvent(type="final", content="unused")


class _FinalThenCleanupErrorEngine(Engine):
    async def build(self, _spec: object) -> None: ...

    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        return RunResult(system_name="stub", visited=["agent"], answer=message)

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        yield RunStreamEvent(type="answer_delta", content="done")
        yield RunStreamEvent(type="final", content="done", route=("agent",))
        raise RuntimeError("cleanup after final")


def test_send_surfaces_sub_agent_in_visited_without_mocking() -> None:
    """End-to-end through the real routes + service: the response exposes the
    sub-agent routing path (the evidence the demo page renders)."""
    app = FastAPI()
    app.state.service = ConversationService(_SubAgentEngine(), MemoryRepository())
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    body = client.post(f"/conversations/{cid}/messages", json={"message": "tags?"}).json()

    assert body["visited"] == ["knowledge_router", "knowledge_router/documentation_agent"]
    assert any("/" in hop for hop in body["visited"]), "expected a sub-agent hop"
    assert "finance" in body["answer"]


def test_stream_surfaces_sse_events_and_persists_final_answer(client: TestClient) -> None:
    cid = client.post("/conversations").json()["conversation_id"]

    with client.stream(
        "POST", f"/conversations/{cid}/messages/stream", json={"message": "hello"}
    ) as response:
        text = "".join(response.iter_text())

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert 'event: answer_delta\ndata: {"type": "answer_delta", "content": "x"}' in text
    assert "event: done\ndata: [DONE]" in text

    messages = client.get(f"/conversations/{cid}/messages").json()
    assert [(m["role"], m["content"]) for m in messages] == [("user", "hello")]


def test_stream_ignores_cleanup_error_after_final() -> None:
    app = FastAPI()
    app.state.service = ConversationService(_FinalThenCleanupErrorEngine(), MemoryRepository())
    app.include_router(router)
    client = TestClient(app)
    cid = client.post("/conversations").json()["conversation_id"]

    with client.stream(
        "POST", f"/conversations/{cid}/messages/stream", json={"message": "x"}
    ) as response:
        text = "".join(response.iter_text())

    assert response.status_code == 200
    assert 'event: final\ndata: {"type": "final", "content": "done", "route": ["agent"]}' in text
    assert "cleanup after final" not in text
    messages = client.get(f"/conversations/{cid}/messages").json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "x"),
        ("assistant", "done"),
    ]


def test_create_accepts_stable_session_and_send_accepts_user(client: TestClient) -> None:
    created = client.post("/conversations", json={"session_id": "sess-1", "user_id": "u1"}).json()
    assert created["conversation_id"] == "sess-1"
    assert created["session_id"] == "sess-1"

    sent = client.post("/conversations/sess-1/messages", json={"message": "hello", "user_id": "u1"})

    assert sent.status_code == 200


def test_feedback_endpoint_records_and_returns_feedback(client: TestClient) -> None:
    cid = client.post("/conversations").json()["conversation_id"]
    send_resp = client.post(f"/conversations/{cid}/messages", json={"message": "hello"}).json()
    assert "message_id" in send_resp
    msg_id = send_resp["message_id"]
    assert msg_id is not None

    # Submit feedback
    fb_resp = client.post(
        f"/conversations/{cid}/messages/{msg_id}/feedback",
        json={"feedback": "thumbs_up"},
    )
    assert fb_resp.status_code == 200
    assert fb_resp.json() == {"message_id": msg_id, "feedback": "thumbs_up"}

    # Retrieve messages and verify feedback is included
    history = client.get(f"/conversations/{cid}/messages").json()
    assistant_msg = next(m for m in history if m["message_id"] == msg_id)
    assert assistant_msg["feedback"] == "thumbs_up"
