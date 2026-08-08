"""HTTP routes via TestClient — stub engine + in-memory repo, no DB or LLM."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_engine.engine.engine import Engine
from agent_engine.engine.types import ChatMessage, RunResult
from agent_engine.runtime.hooks.models import RunContext
from agent_engine.runtime.streaming import RunStreamEvent
from agent_engine.runtime.tool_models import ToolUsageRecord
from agent_manager.api.routes import router
from agent_manager.application import ConversationService
from agent_manager.domain import TokenBudgetUsage, thread_title
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
    u1 = {"X-Agent-Chat-User": "u1"}
    a = client.post("/conversations", headers=u1).json()["conversation_id"]
    client.post(f"/conversations/{a}/messages", json={"message": "first thread"}, headers=u1)
    b = client.post("/conversations", headers=u1).json()["conversation_id"]
    client.post(f"/conversations/{b}/messages", json={"message": "second thread"}, headers=u1)

    threads = client.get("/conversations", headers=u1).json()
    assert {t["conversation_id"]: t["title"] for t in threads} == {
        a: "first thread",
        b: "second thread",
    }
    assert client.get("/conversations", headers={"X-Agent-Chat-User": "u2"}).json() == []
    assert client.get("/conversations").json() == []


def test_another_caller_cannot_touch_a_conversation_it_does_not_own(client: TestClient) -> None:
    """The conversation id is not a credential — every route checks the caller."""
    u1 = {"X-Agent-Chat-User": "u1"}
    cid = client.post("/conversations", headers=u1).json()["conversation_id"]
    client.post(f"/conversations/{cid}/messages", json={"message": "secret"}, headers=u1)

    for headers in ({"X-Agent-Chat-User": "u2"}, {}):
        assert client.get(f"/conversations/{cid}/messages", headers=headers).status_code == 403
        assert client.get(f"/conversations/{cid}/usage", headers=headers).status_code == 403
        send = client.post(
            f"/conversations/{cid}/messages", json={"message": "hi"}, headers=headers
        )
        assert send.status_code == 403
        stream = client.post(
            f"/conversations/{cid}/messages/stream", json={"message": "hi"}, headers=headers
        )
        assert stream.status_code == 403


def test_create_cannot_claim_a_conversation_id_owned_by_another_caller(
    client: TestClient,
) -> None:
    alice = {"X-Agent-Chat-User": "alice"}
    client.post("/conversations", json={"session_id": "sess-1"}, headers=alice)
    client.post("/conversations/sess-1/messages", json={"message": "secret"}, headers=alice)

    for headers in ({"X-Agent-Chat-User": "bob"}, {}):
        taken = client.post("/conversations", json={"session_id": "sess-1"}, headers=headers)
        assert taken.status_code == 409
        assert client.get("/conversations/sess-1/messages", headers=headers).status_code == 403

    assert client.get("/conversations", headers=alice).json()[0]["conversation_id"] == "sess-1"


def test_an_empty_caller_header_is_anonymous_not_an_identity(client: TestClient) -> None:
    """An id of "" would own conversations that no listing can reach."""
    empty = {"X-Agent-Chat-User": "   "}
    cid = client.post("/conversations", headers=empty).json()["conversation_id"]

    assert client.get(f"/conversations/{cid}/messages", headers=empty).status_code == 200
    assert client.get(f"/conversations/{cid}/messages").status_code == 200
    assert client.get("/conversations", headers=empty).json() == []


def test_an_oversized_caller_header_is_rejected(client: TestClient) -> None:
    """The id becomes a 64-char database key, so it is bounded at the edge."""
    assert client.post("/conversations", headers={"X-Agent-Chat-User": "x" * 65}).status_code == 400


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


def test_usage_reports_cumulative_tokens_and_severity_against_budget() -> None:
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
    app.state.service = ConversationService(TokenEngine(), MemoryRepository(), max_tokens=1000)
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    client.post(f"/conversations/{cid}/messages", json={"message": "hi"})

    body = client.get(f"/conversations/{cid}/usage").json()
    assert body["used_tokens"] == 700
    assert body["max_tokens"] == 1000
    assert body["percent"] == pytest.approx(70.0)
    assert body["severity"] == "warning"


def test_token_budget_severity_thresholds() -> None:
    assert TokenBudgetUsage.from_totals(0, None).severity == "normal"
    assert TokenBudgetUsage.from_totals(640, 1000).severity == "normal"
    # Both thresholds are inclusive: exactly 65% warns, exactly 85% is critical.
    assert TokenBudgetUsage.from_totals(650, 1000).severity == "warning"
    assert TokenBudgetUsage.from_totals(849, 1000).severity == "warning"
    assert TokenBudgetUsage.from_totals(850, 1000).severity == "critical"
    assert TokenBudgetUsage.from_totals(5000, 1000).percent == 100.0


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


def test_stream_surfaces_engine_error_raised_after_final() -> None:
    """The engine's own generator failing after `final` is a real failure: it
    reaches the client as an `error` event (the route's own catch-all, not the
    service) and the assistant message is not persisted."""
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
    assert "cleanup after final" in text
    assert "event: done\ndata: [DONE]" in text
    messages = client.get(f"/conversations/{cid}/messages").json()
    assert [(m["role"], m["content"]) for m in messages] == [("user", "x")]


def test_create_accepts_a_stable_session_id_owned_by_the_caller(client: TestClient) -> None:
    u1 = {"X-Agent-Chat-User": "u1"}
    created = client.post("/conversations", json={"session_id": "sess-1"}, headers=u1).json()
    assert created["conversation_id"] == "sess-1"
    assert created["session_id"] == "sess-1"

    sent = client.post("/conversations/sess-1/messages", json={"message": "hello"}, headers=u1)

    assert sent.status_code == 200


class _BudgetEngine(RecordingEngine):
    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        res = await super().run(message, history=history, context=context)
        return dataclasses.replace(res, input_tokens=5, output_tokens=5)


def test_send_returns_429_when_token_budget_exceeded() -> None:
    """send_message returns 429 when the conversation token budget is exhausted."""
    app = FastAPI()
    service = ConversationService(_BudgetEngine(), MemoryRepository(), max_tokens=1)
    app.state.service = service
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    client.post(f"/conversations/{cid}/messages", json={"message": "first"})
    response = client.post(f"/conversations/{cid}/messages", json={"message": "second"})

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["error_type"] == "context_limit_exceeded"
    assert (
        detail["message"]
        == "This conversation has reached its context limit. Start a new chat to continue."
    )


def test_stream_returns_429_when_token_budget_exceeded() -> None:
    """stream_message returns 429 when the conversation token budget is exhausted."""
    app = FastAPI()
    service = ConversationService(_BudgetEngine(), MemoryRepository(), max_tokens=1)
    app.state.service = service
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    client.post(f"/conversations/{cid}/messages", json={"message": "first"})
    response = client.post(f"/conversations/{cid}/messages/stream", json={"message": "second"})

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["error_type"] == "context_limit_exceeded"
    assert (
        detail["message"]
        == "This conversation has reached its context limit. Start a new chat to continue."
    )


class _ToolErrorEngine(RecordingEngine):
    async def run(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> RunResult:
        res = await super().run(message, history=history, context=context)
        err_msg = (
            "HTTPConnectionPool(host='localhost', port=3000): "
            "Max retries exceeded with url: /api/v1/auths/add"
        )
        tool_err = ToolUsageRecord(
            name="add_new_user",
            provider="local",
            status="failed",
            error=err_msg,
        )
        return dataclasses.replace(res, used_tools=(tool_err,))

    async def stream(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage] = (),
        context: RunContext | None = None,
    ) -> AsyncIterator[RunStreamEvent]:
        err_msg = (
            "HTTPConnectionPool(host='localhost', port=3000): "
            "Max retries exceeded with url: /api/v1/auths/add"
        )
        tool_err = ToolUsageRecord(
            name="add_new_user",
            provider="local",
            status="failed",
            error=err_msg,
        )
        yield RunStreamEvent(type="final", content="done", used_tools=(tool_err,))


def test_tool_error_text_is_sanitized_in_send_message() -> None:
    """Raw tool exception details must be sanitized to generic text in API responses."""

    app = FastAPI()
    service = ConversationService(_ToolErrorEngine(), MemoryRepository())
    app.state.service = service
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    response = client.post(f"/conversations/{cid}/messages", json={"message": "trigger tool"})

    assert response.status_code == 200
    used_tools = response.json()["used_tools"]
    assert len(used_tools) == 1
    assert used_tools[0]["error"] == "Tool execution failed"
    assert "localhost" not in used_tools[0]["error"]


def test_tool_error_text_is_sanitized_in_stream_message() -> None:
    """Raw tool exception details must be sanitized to generic text in stream SSE events."""
    app = FastAPI()
    service = ConversationService(_ToolErrorEngine(), MemoryRepository())
    app.state.service = service
    app.include_router(router)
    client = TestClient(app)

    cid = client.post("/conversations").json()["conversation_id"]
    url = f"/conversations/{cid}/messages/stream"
    response = client.post(url, json={"message": "trigger tool"})

    assert response.status_code == 200
    assert "Tool execution failed" in response.text
    assert "localhost" not in response.text
