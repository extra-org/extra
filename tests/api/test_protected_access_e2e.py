"""Issue 151: exercise protected root and child access through the real HTTP app."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.utils import FakeChatModel, fake_model_factory

from agent_engine.api.app import create_app
from agent_engine.engine.langgraph.engine import LangGraphEngine

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
