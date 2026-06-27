"""Static-asset serving + CORS — mount_web on a bare app (no engine/DB)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_manager.api.web import mount_web
from agent_manager.config import Settings


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    mount_web(app, Settings())
    return TestClient(app)


def test_widget_js_served_as_javascript(client: TestClient) -> None:
    r = client.get("/widget.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "agent-chat" in r.text


def test_demo_page_served_as_html(client: TestClient) -> None:
    r = client.get("/demo")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<agent-chat" in r.text


def test_cors_denied_by_default(client: TestClient) -> None:
    r = client.get("/widget.js", headers={"Origin": "https://some-app.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_allowed_for_configured_origin() -> None:
    app = FastAPI()
    mount_web(app, Settings(cors_origins=["https://acmecorp.com"]))
    client = TestClient(app)

    allowed = client.get("/widget.js", headers={"Origin": "https://acmecorp.com"})
    assert allowed.headers["access-control-allow-origin"] == "https://acmecorp.com"

    other = client.get("/widget.js", headers={"Origin": "https://some-app.example"})
    assert "access-control-allow-origin" not in other.headers
