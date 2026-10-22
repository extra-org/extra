"""Worker-only composition enforces controls before any model or provider work."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agent_manager.api.app import create_app
from agent_manager.config import Settings
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelInboxRow,
    ChannelOutboxRow,
)
from tests.agent_manager.test_channel_worker import work as work
from tests.agent_manager.test_channel_worker_lifecycle import worker_values
from tests.agent_manager.test_whatsapp_delivery_lifecycle import outbound_values
from tests.agent_manager.test_whatsapp_pilot_rehearsal import (
    CONFIG,
    SENDER,
    Provider,
    post,
    wait_until,
)
from tests.agent_manager.test_whatsapp_pilot_rehearsal import url as url
from tests.agent_manager.test_whatsapp_webhook import (
    StartupEngine,
    change,
    message,
    payload,
    saved_rows,
)


@pytest.fixture
def runtime(monkeypatch):
    engine = StartupEngine()
    providers: list[Provider] = []

    def sender(*args, **kwargs):
        providers.append(Provider())
        return providers[-1]

    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: engine)
    monkeypatch.setattr("agent_manager.composition.WhatsAppSender", sender)
    return engine, providers


@contextmanager
def deployment(url: str, values: dict) -> Iterator[TestClient]:
    app = create_app(str(CONFIG), Settings.from_values(database_url=url, **values))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        yield client


def receive(client: TestClient, url: str, provider_id: str, text: str) -> ChannelInboxRow:
    post(client, payload(change(message(provider_id, text={"body": text}))))

    def completed() -> ChannelInboxRow | None:
        return next(
            (
                row
                for row in saved_rows(url, ChannelInboxRow)
                if row.provider_message_id == provider_id
                and row.state in {"answered", "done", "failed", "needs_review"}
            ),
            None,
        )

    wait_until(lambda: completed() is not None)
    row = completed()
    assert row is not None
    return row


@pytest.mark.parametrize("support_contact", [None, "support@example.test"])
def test_paused_stop_blocks_execution_and_live_restart_cannot_send_old_replies(
    url: str, runtime, support_contact: str | None
):
    engine, providers = runtime
    paused = worker_values(extra_whatsapp_support_contact=support_contact)
    with deployment(url, paused) as client:
        assert receive(client, url, "before", "before stop").state == "answered"
        assert receive(client, url, "stop", "/stop").state == "done"
        assert receive(client, url, "during", "while stopped").state == "done"
        assert engine.prompts == ["before stop"]
        assert receive(client, url, "start", "/start").state == "answered"
        assert receive(client, url, "after", "after start").state == "answered"
    assert providers == []
    assert engine.prompts == ["before stop", "after start"]

    with deployment(url, outbound_values()):
        wait_until(lambda: bool(providers and len(providers[0].calls) >= 2))
        assert providers[0].calls == [
            (SENDER, "קבלת ההודעות חודשה. אפשר לשלוח הודעת טקסט."),
            (SENDER, "answer:after start"),
        ]
    assert providers[0].closed
    assert engine.prompts == ["before stop", "after start"]


def test_paused_reset_starts_a_conversation_without_previous_history(url: str, runtime):
    engine, providers = runtime
    with deployment(url, worker_values()) as client:
        first = receive(client, url, "first", "first question")
        assert first.state == "answered"
        reset = receive(client, url, "reset", "/new")
        assert reset.state == "answered" and reset.run_id is None
        second = receive(client, url, "second", "second question")
        assert second.state == "answered"
        assert first.conversation_id and second.conversation_id != first.conversation_id
    assert engine.prompts == ["first question", "second question"]
    assert engine.histories == [(), ()]
    assert providers == []


@pytest.mark.parametrize("support_contact", [None, "support@example.test"])
def test_paused_help_does_not_invent_a_support_contact(url: str, runtime, support_contact):
    engine, providers = runtime
    with deployment(url, worker_values(extra_whatsapp_support_contact=support_contact)) as client:
        item = receive(client, url, "help", "/help")
        replies = saved_rows(url, ChannelOutboxRow)
        assert item.run_id is None
        if support_contact is None:
            assert item.state == "done" and replies == []
        else:
            assert item.state == "answered"
            assert [reply.text for reply in replies] == ["לתמיכה אנושית: support@example.test"]
    assert engine.prompts == [] and providers == []


async def test_worker_rejects_missing_control_storage(work):
    from agent_manager.composition import application_repositories, channel_worker_lifespan

    settings = Settings.from_values(**worker_values(database_url=str(work.database.url)))
    async with application_repositories(settings) as repositories:
        with pytest.raises(ValueError, match="control storage"):
            async with channel_worker_lifespan(
                settings, replace(repositories, controls=None), work.service, "system"
            ):
                pytest.fail("worker started without enforcing controls")
