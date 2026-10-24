"""Issue 151: exercise protected root and child access through the real HTTP app."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from tests.agent_manager.conftest import bearer, build_test_app
from tests.approvals.test_engine_hitl import _multi_tool_spec
from tests.approvals.test_resume_binding import write_tool
from tests.engine.test_engine_flow import orchestrator, system
from tests.fixtures.utils import FakeChatModel, fake_model_factory

from agent_engine.api.app import create_app
from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.models import RunStatus
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_manager.application import ConversationService
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository

_FORGED_HEADERS = {
    "X-User-ID": "admin",
    "X-Roles": "admin",
    "Authorization": "Bearer forged-admin-token",
}
_MODEL = "    model: {provider: openai, name: test-model}\n"


def _client(
    base_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    root_kind: str = "agent",
    protected_root: bool = True,
    allow: bool = False,
) -> TestClient:
    plugins = base_dir / "plugins"
    plugins.mkdir()
    (plugins / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        f"        return {allow!r}\n",
        encoding="utf-8",
    )
    if root_kind == "agent":
        declarations = (
            "agents:\n  admin:\n    description: Administrative agent.\n"
            f"    protected: {str(protected_root).lower()}\n{_MODEL}"
        )
        graph = "graph:\n  admin:\n"
    else:
        (base_dir / "route.md").write_text("Delegate to admin.", encoding="utf-8")
        declarations = (
            "orchestrators:\n  root:\n    description: Routes requests.\n"
            f"    protected: {str(protected_root).lower()}\n"
            f"    prompts: {{orchestrator: route.md}}\n{_MODEL}"
            f"agents:\n  admin:\n    description: Administrative agent.\n{_MODEL}"
            "    protected: true\n"
        )
        graph = "graph:\n  root:\n    admin:\n"
    config = base_dir / "agents.yaml"
    config.write_text(f"system: {{name: Access Probe}}\n{declarations}{graph}", encoding="utf-8")
    # Keep startup, validation, graph compilation and request execution real;
    # replace only model construction at the engine's existing injection seam.
    monkeypatch.setattr(
        "agent_engine.api.app.LangGraphEngine",
        lambda *args, **kwargs: LangGraphEngine(*args, model_factory=fake_model_factory, **kwargs),
    )
    return TestClient(create_app(str(config)))


@pytest.fixture
def model_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[Any]]:
    calls: list[list[Any]] = []
    respond = FakeChatModel._respond

    def record(model: FakeChatModel, messages: list[Any]):
        calls.append(messages)
        return respond(model, messages)

    monkeypatch.setattr(FakeChatModel, "_respond", record)
    return calls


@pytest.mark.parametrize("root_kind", ["agent", "orchestrator"])
@pytest.mark.parametrize("endpoint", ["/invoke", "/stream"])
def test_denied_protected_root_never_executes_over_http(
    tmp_path, monkeypatch, model_calls, root_kind, endpoint
):
    with _client(tmp_path, monkeypatch, root_kind=root_kind) as client:
        response = client.post(endpoint, json={"message": "admin"}, headers=_FORGED_HEADERS)

    assert response.status_code == 200
    if endpoint == "/invoke":
        body = response.json()
        assert body["visited"] == []
        assert body["answer"] == "Access denied."
        assert body["used_tools"] == []
    else:
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == ["answer_delta", "final"]
        assert events[0]["content"] == "Access denied."
        assert events[1]["route"] == []
        assert events[1]["content"] == "Access denied."
    assert model_calls == []


@pytest.mark.parametrize("root_kind", ["agent", "orchestrator"])
def test_protected_root_executes_when_policy_allows(tmp_path, monkeypatch, root_kind):
    with _client(tmp_path, monkeypatch, root_kind=root_kind, allow=True) as client:
        response = client.post("/invoke", json={"message": "admin"})

    assert response.status_code == 200
    assert response.json()["visited"] == (
        ["admin"] if root_kind == "agent" else ["root", "root/admin"]
    )


@pytest.mark.parametrize("allow", [False, True])
def test_protected_child_still_obeys_policy_with_forged_headers(tmp_path, monkeypatch, allow):
    with _client(
        tmp_path, monkeypatch, root_kind="orchestrator", protected_root=False, allow=allow
    ) as client:
        response = client.post("/invoke", json={"message": "admin"}, headers=_FORGED_HEADERS)

    assert response.status_code == 200
    assert response.json()["visited"] == (["root", "root/admin"] if allow else ["root"])


def test_unprotected_root_still_executes_with_deny_all_policy(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, protected_root=False) as client:
        response = client.post("/invoke", json={"message": "hello"})

    assert response.status_code == 200
    assert response.json()["visited"] == ["admin"]
    assert response.json()["answer"] == "ok"


@pytest.mark.parametrize("location", ["agent", "orchestrator", "child"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("permitted", [False, True])
async def test_manager_approval_uses_current_verified_roles(
    tmp_path, location, streaming, permitted
):
    output = tmp_path / "executed.log"
    write_tool(tmp_path, "send_email", output)
    (tmp_path / "plugins" / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        "        return 'admin' in ctx.get('auth', {}).get('roles', ())\n",
        encoding="utf-8",
    )
    spec = _multi_tool_spec("send_email")
    if location in {"agent", "child"}:
        spec = replace(
            spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True))
        )
    if location != "agent":
        spec = system(orchestrator("root", [spec.graph]))
        if location == "orchestrator":
            spec = replace(
                spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True))
            )
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        service = ConversationService(engine, MemoryRepository())
        app = build_test_app(service)
        initial = bearer("owner", roles=["admin"], org_id="org")
        current = bearer("owner", roles=["admin"] if permitted else [], org_id="org")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/conversations", json={"session_id": "session"}, headers=initial
            )
            assert response.status_code == 200
            response = await client.post(
                "/conversations/session/messages", json={"message": "hello"}, headers=initial
            )
            assert response.status_code == 200
            pending = response.json()["pending_approval"]
            assert pending is not None and not output.exists()
            suffix = "/stream" if streaming else ""
            response = await client.post(
                f"/conversations/session/runs/{pending['run_id']}/approvals/"
                f"{pending['approval_id']}/decision{suffix}",
                json={"decision": ApprovalDecision.ALLOW_ONCE.value},
                headers={**current, "X-Roles": "admin", "X-User-ID": "owner"},
            )

        if permitted:
            assert response.status_code == 200
            assert len(output.read_text().splitlines()) == 1
            assert await engine.get_run_status(pending["run_id"]) == RunStatus.COMPLETED
        else:
            assert not output.exists()
            if streaming:
                assert response.status_code == 200
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ") and line != "data: [DONE]"
                ]
                assert any(event["type"] == "error" for event in events)
                assert not any(event["type"] == "final" for event in events)
            else:
                # Removing a child changes the replayed model tool set, which
                # the existing checkpoint guard rejects before tool execution.
                assert response.status_code == (500 if location == "child" else 409)
            assert await engine.get_run_status(pending["run_id"]) == RunStatus.FAILED
            assert await engine.get_pending_approval(pending["run_id"]) is None
