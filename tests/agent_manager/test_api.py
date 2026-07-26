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


def test_unknown_conversation_returns_404(client: TestClient) -> None:
    assert client.get("/conversations/nope/messages").status_code == 404
    assert client.post("/conversations/nope/messages", json={"message": "x"}).status_code == 404


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


def test_send_returns_429_when_token_budget_exceeded() -> None:
    """send_message returns 429 when the conversation token budget is exhausted."""
    app = FastAPI()
    # max_tokens=1 means the budget is treated as exceeded as soon as any token is recorded.
    # To trigger the guard we first send a real message (consumes tokens), then send a second one.
    service = ConversationService(RecordingEngine(), MemoryRepository(), max_tokens=1)
    app.state.service = service
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    # First message succeeds and records token usage in the repository.
    client.post(f"/conversations/{cid}/messages", json={"message": "first"})
    # Second message should be rejected because the budget is now exhausted.
    response = client.post(f"/conversations/{cid}/messages", json={"message": "second"})

    assert response.status_code == 429
    assert "budget" in response.json()["detail"]


def test_stream_returns_429_when_token_budget_exceeded() -> None:
    """stream_message returns 429 when the conversation token budget is exhausted."""
    app = FastAPI()
    service = ConversationService(RecordingEngine(), MemoryRepository(), max_tokens=1)
    app.state.service = service
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    # Consume the budget with a non-streaming send first.
    client.post(f"/conversations/{cid}/messages", json={"message": "first"})
    # The streaming endpoint should now return 429.
    response = client.post(f"/conversations/{cid}/messages/stream", json={"message": "second"})

    assert response.status_code == 429
    assert "budget" in response.json()["detail"]
