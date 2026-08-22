"""HTTP-level regression coverage for the approval endpoints.

``ApprovalDecisionRequest`` declares every field optional, so a bare POST
with no JSON body should be accepted by ``/approve`` and ``/reject`` exactly
like ``{}`` is. Uses a deterministic fake chat model (no LLM/network) wired
through ``create_app`` via a real, minimal ``agents.yaml``.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall as LCToolCall

from agent_engine.api.app import create_app


class FakeChatModel:
    """Calls a fixed tool once, then answers from its result."""

    def __init__(self, tool_names: list[str] | None = None) -> None:
        self._tool_names = tool_names or []

    def bind_tools(self, tools: list[Any]) -> FakeChatModel:
        return FakeChatModel([t.name for t in tools])

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: list[Any]) -> AsyncIterator[AIMessage]:
        yield self._respond(messages)

    def _respond(self, messages: list[Any]) -> AIMessage:
        if self._tool_names and not any(isinstance(m, ToolMessage) for m in messages):
            input_text = next(
                (str(m.content) for m in messages if isinstance(m, HumanMessage)), "message"
            )
            return AIMessage(
                content="",
                tool_calls=[
                    LCToolCall(
                        name=self._tool_names[0],
                        args={"message": "go"},
                        id=f"call_{input_text}",
                    )
                ],
            )
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                return AIMessage(content=f"done: {m.content}")
        return AIMessage(content="done")


class FailingAfterApprovalModel(FakeChatModel):
    """Pause for approval, then fail without exposing the internal message."""

    def bind_tools(self, tools: list[Any]) -> FailingAfterApprovalModel:
        return FailingAfterApprovalModel([t.name for t in tools])

    def _respond(self, messages: list[Any]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            raise RuntimeError("private failure after approval")
        return super()._respond(messages)


def _write_config(base_dir: Path) -> Path:
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "send_email.py").write_text(
        "def send_email(message: str) -> str:\n    return 'sent: ' + message\n",
        encoding="utf-8",
    )

    config_path = base_dir / "agents.yaml"
    config_path.write_text(
        """
system:
  name: Approval Test System

tools:
  send_email:
    description: Send an email.

agents:
  writer:
    description: Writes and sends emails.
    model:
      provider: openai
      name: gpt-4o-mini
    tools: [send_email]

graph:
  writer:
""",
        encoding="utf-8",
    )
    return config_path


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # ``LangGraphEngine.__init__`` binds its default ``model_factory`` to the
    # real ``build_chat_model`` at class-definition time, so patching that name
    # after import has no effect. Instead, let the real factory construct a
    # genuine (but never-called) ``ChatOpenAI`` instance, and patch its
    # ``bind_tools`` at the class level — that's looked up per-call via the
    # class, not bound early, so it intercepts regardless of construction time.
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-credential")
    monkeypatch.setattr(
        ChatOpenAI, "bind_tools", lambda self, tools, **_: FakeChatModel([t.name for t in tools])
    )

    config_path = _write_config(tmp_path)
    app = create_app(str(config_path))
    with TestClient(app) as test_client:
        yield test_client


def _trigger_pending_approval(
    client: TestClient, *, session_id: str | None = None
) -> tuple[str, str]:
    headers = {"X-Session-ID": session_id} if session_id is not None else None
    response = client.post("/invoke", json={"message": "hi"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    pending = body["pending_approval"]
    return body["run_id"], pending["approval_id"]


def _write_token_probe_config(base_dir: Path) -> Path:
    """A tool that reports whichever credential it sees at call time, so a
    test can tell a run's original token apart from the approver's."""
    tools_dir = base_dir / "plugins" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "send_email.py").write_text(
        "from agent_engine.runtime.hooks import current_run_context\n\n"
        "def send_email(message: str) -> str:\n"
        "    ctx = current_run_context.get()\n"
        "    auth = ctx.auth_context if ctx else None\n"
        "    token = auth.inbound_access_token if auth else None\n"
        "    return f'sent as {token}'\n",
        encoding="utf-8",
    )
    config_path = base_dir / "agents.yaml"
    config_path.write_text(
        """
system:
  name: Approval Test System

tools:
  send_email:
    description: Send an email.

agents:
  writer:
    description: Writes and sends emails.
    model:
      provider: openai
      name: gpt-4o-mini
    tools: [send_email]

graph:
  writer:
""",
        encoding="utf-8",
    )
    return config_path


@pytest.fixture
def token_probe_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-credential")
    monkeypatch.setattr(
        ChatOpenAI, "bind_tools", lambda self, tools, **_: FakeChatModel([t.name for t in tools])
    )
    config_path = _write_token_probe_config(tmp_path)
    app = create_app(str(config_path))
    with TestClient(app) as test_client:
        yield test_client


def test_approval_resumes_with_the_approvers_token_not_the_original_caller(
    token_probe_client: TestClient,
) -> None:
    """A run started by one caller may be approved by another — the tool call
    that finally executes must act with the approver's credential, not
    whatever the run happened to start with (extra-org/extra#112 review)."""
    started = token_probe_client.post(
        "/invoke", json={"message": "hi"}, headers={"Authorization": "Bearer TOKEN_A"}
    )
    assert started.status_code == 200
    pending = started.json()["pending_approval"]
    run_id, approval_id = started.json()["run_id"], pending["approval_id"]

    approved = token_probe_client.post(
        f"/runs/{run_id}/approvals/{approval_id}/approve",
        headers={"Authorization": "Bearer TOKEN_B"},
    )

    assert approved.status_code == 200
    assert "sent as TOKEN_B" in approved.json()["answer"]


def test_approve_with_empty_json_body_succeeds(client: TestClient) -> None:
    run_id, approval_id = _trigger_pending_approval(client)

    response = client.post(f"/runs/{run_id}/approvals/{approval_id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_approve_with_no_body_at_all_succeeds(client: TestClient) -> None:
    run_id, approval_id = _trigger_pending_approval(client)

    response = client.post(f"/runs/{run_id}/approvals/{approval_id}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_duplicate_approval_recovers_the_original_result(client: TestClient) -> None:
    """A retried decision returns the first result, not a bare 409.

    A client that times out and retries has no way to read 409 as "your
    decision already succeeded". ConversationService already recovers via
    get_processed_result for this case; the HTTP layer must match.
    """
    run_id, approval_id = _trigger_pending_approval(client)

    first = client.post(f"/runs/{run_id}/approvals/{approval_id}/approve")
    assert first.status_code == 200

    duplicate = client.post(f"/runs/{run_id}/approvals/{approval_id}/approve")

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == first.json()["status"]
    assert duplicate.json()["answer"] == first.json()["answer"]


def test_reject_with_no_body_at_all_succeeds(client: TestClient) -> None:
    run_id, approval_id = _trigger_pending_approval(client)

    response = client.post(f"/runs/{run_id}/approvals/{approval_id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_decision_endpoint_still_requires_a_body(client: TestClient) -> None:
    run_id, approval_id = _trigger_pending_approval(client)

    response = client.post(f"/runs/{run_id}/approvals/{approval_id}/decision")

    assert response.status_code == 422


def test_session_bound_approval_requires_the_same_session(client: TestClient) -> None:
    run_id, approval_id = _trigger_pending_approval(client, session_id="session-1")

    omitted = client.post(f"/runs/{run_id}/approvals/{approval_id}/approve")
    wrong = client.post(
        f"/runs/{run_id}/approvals/{approval_id}/approve",
        headers={"X-Session-ID": "session-2"},
    )
    allowed = client.post(
        f"/runs/{run_id}/approvals/{approval_id}/approve",
        headers={"X-Session-ID": "session-1"},
    )

    assert omitted.status_code == 403
    assert omitted.json()["detail"] == "not authorized to decide this approval"
    assert approval_id not in omitted.text
    assert wrong.status_code == 403
    assert allowed.status_code == 200


def test_approval_endpoint_sanitizes_unexpected_resume_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-credential")
    monkeypatch.setattr(
        ChatOpenAI,
        "bind_tools",
        lambda self, tools, **_: FailingAfterApprovalModel([tool.name for tool in tools]),
    )
    logged_exceptions: list[BaseException] = []

    def capture_exception(*args: object, **kwargs: object) -> None:
        del args, kwargs
        exc = sys.exc_info()[1]
        assert exc is not None
        logged_exceptions.append(exc)

    monkeypatch.setattr("agent_engine.api.app.logger.exception", capture_exception)
    app = create_app(str(_write_config(tmp_path)))

    with TestClient(app) as test_client:
        run_id, approval_id = _trigger_pending_approval(test_client)
        response = test_client.post(f"/runs/{run_id}/approvals/{approval_id}/approve")

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "private failure after approval" not in response.text
    assert [str(exc) for exc in logged_exceptions] == ["private failure after approval"]
