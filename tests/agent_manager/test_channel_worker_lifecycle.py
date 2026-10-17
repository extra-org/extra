"""Opt-in worker lifecycle through the real manager HTTP and file-SQL wiring."""

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import SQLModel

from agent_manager.api.app import create_app
from agent_manager.config import Settings
from agent_manager.domain import Principal
from agent_manager.domain.channels.identity import ChannelIdentityResolver
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelInboxRow,
    ChannelOutboxRow,
)
from agent_manager.infrastructure.persistence.database import create_db_engine
from tests.agent_manager.test_whatsapp_webhook import (
    ENDPOINT,
    StartupEngine,
    change,
    encoded,
    message,
    payload,
    saved_rows,
    settings_values,
    signed,
)

PLUGIN = "tests.agent_manager.test_channel_worker_lifecycle:OrganizationIdentityResolver"
ALICE = Principal.external("alice", organization_id="org", roles=("employee",))


class OrganizationIdentityResolver(ChannelIdentityResolver):
    closed = False
    instances = 0

    def __init__(self):
        type(self).instances += 1

    async def resolve(self, address):
        return replace(ALICE, access_token="test-only-delegated-token")

    async def close(self):
        type(self).closed = True


class BrokenIdentityResolver(ChannelIdentityResolver):
    def __init__(self):
        raise RuntimeError("private-plugin-credential")

    async def resolve(self, address):
        return None


class CleanupErrorResolver(OrganizationIdentityResolver):
    async def close(self):
        raise RuntimeError("private-cleanup-credential")


class SyncCleanupResolver(OrganizationIdentityResolver):
    def close(self):
        pass


def worker_values(**overrides):
    return settings_values(
        extra_whatsapp_worker_enabled=True,
        extra_whatsapp_organization_id="org",
        extra_whatsapp_identity_resolver=PLUGIN,
        extra_whatsapp_worker_poll_seconds=0.02,
        **overrides,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"extra_whatsapp_organization_id": None},
        {"extra_whatsapp_identity_resolver": None},
        {"extra_whatsapp_enabled": False},
        {"extra_whatsapp_identity_resolver": "bad import path"},
    ],
)
def test_enabling_worker_requires_explicit_identity_and_intake(overrides):
    with pytest.raises(ValidationError):
        Settings.from_values(**{**worker_values(), **overrides})


def test_worker_consumes_once_and_keeps_the_next_turns_history(tmp_path: Path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"

    async def schema():
        database = create_db_engine(url)
        async with database.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        await database.dispose()

    asyncio.run(schema())
    engine = StartupEngine()
    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: engine)
    config = (
        Path(__file__).resolve().parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
    )
    settings = Settings.from_values(database_url=url, **worker_values())
    app = create_app(str(config), settings)
    OrganizationIdentityResolver.closed = False
    OrganizationIdentityResolver.instances = 0
    with TestClient(app) as client:
        first = encoded(payload(change(message("one", text={"body": "first"}))))
        assert client.post(ENDPOINT, content=first, headers=signed(first)).status_code == 200
        assert client.post(ENDPOINT, content=first, headers=signed(first)).status_code == 200
        wait_for_answers(url, 1)
        second = encoded(payload(change(message("two", text={"body": "second"}))))
        assert client.post(ENDPOINT, content=second, headers=signed(second)).status_code == 200
        wait_for_answers(url, 2)
        assert engine.prompts == ["first", "second"]
        assert [(m.role, m.content) for m in engine.histories[1]] == [
            ("user", "first"),
            ("assistant", "answer:first"),
        ]
        assert engine.builds == 1
        for context in engine.contexts:
            assert context is not None and context.auth_context is not None
            assert context.auth_context.inbound_access_token == "test-only-delegated-token"
        persisted = "".join(row.model_dump_json() for row in saved_rows(url, ChannelInboxRow))
        assert "test-only-delegated-token" not in persisted
    assert OrganizationIdentityResolver.closed
    assert OrganizationIdentityResolver.instances == 1
    assert app.state.channel_worker.running is False


def wait_for_answers(url, count):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        rows = saved_rows(url, ChannelOutboxRow)
        if len(rows) == count:
            return
        time.sleep(0.01)
    pytest.fail(f"worker did not stage {count} answers")


@pytest.mark.parametrize(
    "plugin",
    [
        "not_an_installed_module:Resolver",
        "tests.agent_manager.test_channel_worker_lifecycle:BrokenIdentityResolver",
        "tests.agent_manager.test_channel_worker_lifecycle:StartupEngine",
        "tests.agent_manager.test_channel_worker_lifecycle:SyncCleanupResolver",
    ],
)
def test_invalid_identity_plugin_fails_startup_without_secret_text(
    tmp_path: Path, monkeypatch, plugin
):
    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: StartupEngine())
    config = (
        Path(__file__).resolve().parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
    )
    settings = Settings.from_values(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'invalid.db'}",
        **{**worker_values(), "extra_whatsapp_identity_resolver": plugin},
    )
    with pytest.raises(ValueError) as error, TestClient(create_app(str(config), settings)):
        pass
    assert "private-plugin-credential" not in str(error.value)


def test_identity_cleanup_error_is_sanitized_without_preventing_manager_shutdown(
    tmp_path, monkeypatch, caplog
):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}"

    async def schema():
        database = create_db_engine(url)
        async with database.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        await database.dispose()

    asyncio.run(schema())
    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: StartupEngine())
    config = (
        Path(__file__).resolve().parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
    )
    settings = Settings.from_values(
        database_url=url,
        **{
            **worker_values(),
            "extra_whatsapp_identity_resolver": (
                "tests.agent_manager.test_channel_worker_lifecycle:CleanupErrorResolver"
            ),
        },
    )
    with TestClient(create_app(str(config), settings)) as client:
        assert client.get("/health").status_code == 200
    assert "private-cleanup-credential" not in caplog.text


def test_failed_supervisor_is_reported_as_unhealthy(tmp_path, monkeypatch):
    from agent_manager.domain.channels.ownership import ChannelWorkerOwnershipLost
    from agent_manager.infrastructure.persistence.channels.ownership import (
        SqlChannelWorkerOwnership,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'health.db'}"

    async def schema():
        database = create_db_engine(url)
        async with database.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        await database.dispose()

    asyncio.run(schema())
    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: StartupEngine())
    config = (
        Path(__file__).resolve().parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
    )
    app = create_app(str(config), Settings.from_values(database_url=url, **worker_values()))

    async def lost(self):
        raise ChannelWorkerOwnershipLost("injected ownership loss")

    response = None
    with pytest.raises(ChannelWorkerOwnershipLost), TestClient(app) as client:
        monkeypatch.setattr(SqlChannelWorkerOwnership, "check", lost)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = client.get("/health")
            if response.status_code == 503:
                break
            time.sleep(0.01)
    assert response is not None and response.status_code == 503
    assert response.json()["status"] == "degraded"
