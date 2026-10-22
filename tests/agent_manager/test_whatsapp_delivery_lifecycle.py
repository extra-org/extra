"""Opt-in composition: signed intake to a fake provider, with one shared engine."""

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import SQLModel

from agent_manager.api.app import create_app
from agent_manager.application.channels.worker import ChannelWorker
from agent_manager.config import Settings
from agent_manager.domain.channels.delivery import SendOutcome
from agent_manager.domain.channels.ownership import ChannelWorkerAlreadyRunning
from agent_manager.infrastructure.persistence.channels.ownership import SqlChannelWorkerOwnership
from agent_manager.infrastructure.persistence.database import create_db_engine
from tests.agent_manager.test_channel_delivery import Sender, pump
from tests.agent_manager.test_channel_delivery_storage import answered
from tests.agent_manager.test_channel_delivery_storage import delivery as delivery
from tests.agent_manager.test_channel_worker import OPTIONS
from tests.agent_manager.test_channel_worker import work as work
from tests.agent_manager.test_channel_worker_lifecycle import worker_values
from tests.agent_manager.test_whatsapp_webhook import (
    ENDPOINT,
    StartupEngine,
    change,
    encoded,
    message,
    payload,
    signed,
)


def outbound_values(**overrides):
    return {
        **worker_values(),
        "extra_whatsapp_sender_enabled": True,
        "extra_whatsapp_api_version": "v99.0",
        "extra_whatsapp_access_token": "test-outbound-secret",
        "extra_whatsapp_support_contact": "support@example.test",
        **overrides,
    }


@pytest.mark.parametrize(
    "override",
    [
        {"extra_whatsapp_worker_enabled": False},
        {"extra_whatsapp_access_token": None},
        {"extra_whatsapp_api_version": None},
        {"extra_whatsapp_api_version": "latest"},
        {"extra_whatsapp_support_contact": None},
        {"extra_whatsapp_access_token": "test-only-whatsapp-app-secret"},
    ],
)
def test_sender_enablement_requires_explicit_safe_configuration(override):
    with pytest.raises(ValidationError):
        Settings.from_values(**outbound_values(**override))


def test_outbound_token_is_excluded_from_settings_representations():
    settings = Settings.from_values(**outbound_values())
    secret = settings.extra_whatsapp_access_token
    assert secret is not None and secret.get_secret_value() == "test-outbound-secret"
    assert "test-outbound-secret" not in repr(settings)
    assert "extra_whatsapp_access_token" not in settings.model_dump()


def test_signed_intake_runs_once_sends_answer_and_handles_help_without_model(
    tmp_path: Path, monkeypatch
):
    url = f"sqlite+aiosqlite:///{tmp_path / 'roundtrip.db'}"

    async def schema():
        database = create_db_engine(url)
        try:
            async with database.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
        finally:
            await database.dispose()

    asyncio.run(schema())
    engine = StartupEngine()

    class Provider:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.closed = False

        async def send(self, recipient, text):
            self.calls.append((recipient, text))
            return SendOutcome("accepted", f"wamid.{len(self.calls)}")

        async def close(self):
            self.closed = True

    provider = Provider()
    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: engine)
    monkeypatch.setattr("agent_manager.composition.WhatsAppSender", lambda *a, **kw: provider)
    settings = Settings.from_values(**outbound_values(database_url=url))
    # Keep the plugin namespace shared with the other in-process HTTP fixtures.
    config = Path(__file__).parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
    app = create_app(str(config), settings)
    with TestClient(app) as client:
        for provider_id, body, expected in (
            ("question", "hello", "answer:hello"),
            ("help", "/help", "support@example.test"),
        ):
            raw = encoded(payload(change(message(provider_id, text={"body": body}))))
            assert client.post(ENDPOINT, content=raw, headers=signed(raw)).status_code == 200
            deadline = time.monotonic() + 3
            while not any(expected in text for _, text in provider.calls):
                assert time.monotonic() < deadline
                time.sleep(0.01)
        assert client.get("/health").status_code == 200
    assert engine.builds == 1 and engine.prompts == ["hello"]
    assert provider.closed


async def test_shutdown_keeps_ownership_until_send_cleanup_and_unknown_receipt_finish(
    work, delivery
):
    started, closing, release = (asyncio.Event() for _ in range(3))

    class Provider(Sender):
        async def send(self, recipient, text):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()

    item = await answered(work)
    url = str(work.database.url)
    worker = ChannelWorker(
        work.repository,
        work.processor(),
        SqlChannelWorkerOwnership(url, "connection"),
        replace(OPTIONS, drain_seconds=0.01),
        delivery=pump(work, delivery, Provider()),
    )
    await worker.__aenter__()
    await asyncio.wait_for(started.wait(), 2)
    stopped = asyncio.create_task(worker.__aexit__(None, None, None))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        with pytest.raises(ChannelWorkerAlreadyRunning):
            async with SqlChannelWorkerOwnership(url, "connection").hold():
                pytest.fail("send cleanup lost ownership")
    finally:
        release.set()
        await stopped
    assert (await delivery.parts(item.event_id))[0].state == "delivery_unknown"
    async with SqlChannelWorkerOwnership(url, "connection").hold():
        pass


async def test_ownership_failure_before_delivery_is_fatal_without_waiting_for_next_poll(
    work, delivery
):
    from contextlib import suppress

    from agent_manager.domain.channels.ownership import ChannelWorkerOwnershipLost
    from tests.agent_manager.test_channel_worker import Ownership

    class LostBeforeSend(Ownership):
        calls = 0

        async def check(self):
            self.calls += 1
            if self.calls >= 3:
                raise ChannelWorkerOwnershipLost("injected ownership loss")

    await answered(work)
    sender = Sender()
    worker = ChannelWorker(
        work.repository,
        work.processor(),
        LostBeforeSend(),
        replace(OPTIONS, poll_seconds=60),
        delivery=pump(work, delivery, sender),
    )
    await worker.__aenter__()
    try:
        async with asyncio.timeout(1):
            while worker.running:
                await asyncio.sleep(0.005)
        assert sender.calls == []
    finally:
        with suppress(ChannelWorkerOwnershipLost):
            await worker.__aexit__(None, None, None)


@pytest.mark.parametrize(
    "sender_enabled,blocked_client", [(True, "sender"), (True, "identity"), (False, "identity")]
)
async def test_lifespan_joins_client_cleanup_through_repeated_cancellation(
    work, monkeypatch, sender_enabled, blocked_client
):
    from agent_manager.composition import application_repositories, channel_worker_lifespan
    from tests.agent_manager.test_channel_worker import Identities

    ready, shutdown, closing, release = (asyncio.Event() for _ in range(4))
    closed = []

    async def close_client(name):
        if name == blocked_client:
            closing.set()
            await release.wait()
        closed.append(name)

    class Identity(Identities):
        async def close(self):
            await close_client("identity")

    class Provider(Sender):
        async def close(self):
            await close_client("sender")

    monkeypatch.setattr(
        "agent_manager.composition.load_channel_identity_resolver", lambda _: Identity()
    )
    monkeypatch.setattr("agent_manager.composition.WhatsAppSender", lambda *a: Provider())
    settings = Settings.from_values(
        **outbound_values(
            database_url=str(work.database.url), extra_whatsapp_sender_enabled=sender_enabled
        )
    )

    async def lifetime():
        async with (
            application_repositories(settings) as repositories,
            channel_worker_lifespan(settings, repositories, work.service, "system"),
        ):
            ready.set()
            await shutdown.wait()

    task = asyncio.create_task(lifetime())
    try:
        await asyncio.wait_for(ready.wait(), 2)
        shutdown.set()
        await asyncio.wait_for(closing.wait(), 2)
        for _ in range(2):
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.02)
            assert not done, "lifespan returned while a channel client was still closing"
        assert blocked_client not in closed
    finally:
        release.set()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError)
    assert closed == (["sender", "identity"] if sender_enabled else ["identity"])
