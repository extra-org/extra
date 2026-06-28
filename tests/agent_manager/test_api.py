"""HTTP routes via TestClient — stub engine + in-memory repo, no DB or LLM."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_create_accepts_stable_session_and_send_accepts_user(client: TestClient) -> None:
    created = client.post("/conversations", json={"session_id": "sess-1", "user_id": "u1"}).json()
    assert created["conversation_id"] == "sess-1"
    assert created["session_id"] == "sess-1"

    sent = client.post("/conversations/sess-1/messages", json={"message": "hello", "user_id": "u1"})

    assert sent.status_code == 200
