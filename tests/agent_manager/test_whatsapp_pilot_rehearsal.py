"""Pilot rehearsal: paused intake survives restarts and is answered and sent exactly once."""

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from agent_engine.core.validator import SystemSpecValidator
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_manager.api.app import create_app
from agent_manager.config import Settings
from agent_manager.domain.channels.delivery import SendOutcome
from agent_manager.domain.channels.identity import ChannelIdentityUnavailable
from agent_manager.domain.channels.models import ChannelAddress
from agent_manager.infrastructure.channels.identity_loader import load_channel_identity_resolver
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryPartRow,
    ChannelInboxRow,
)
from agent_manager.infrastructure.persistence.database import create_db_engine
from tests.agent_manager.test_whatsapp_delivery_lifecycle import outbound_values
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

CONFIG = Path(__file__).parents[2] / "examples/enterprise-knowledge-assistant/agents.yaml"
SENDER = "opaque-sender"


class Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    async def send(self, recipient: str, text: str) -> SendOutcome:
        self.calls.append((recipient, text))
        return SendOutcome("accepted", f"wamid.reply-{len(self.calls)}")

    async def close(self) -> None:
        self.closed = True


def wait_until(condition: Callable[[], bool], seconds: float = 3) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "rehearsal step did not complete in time"
        time.sleep(0.01)


def post(client: TestClient, value: dict) -> None:
    body = encoded(value)
    assert client.post(ENDPOINT, content=body, headers=signed(body)).status_code == 200


@pytest.fixture
def url(tmp_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{tmp_path / 'pilot.db'}"

    async def schema() -> None:
        database = create_db_engine(url)
        try:
            async with database.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
        finally:
            await database.dispose()

    asyncio.run(schema())
    return url


def test_paused_intake_is_answered_once_after_restart_and_delivery_is_projected(
    url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine = StartupEngine()
    providers: list[Provider] = []

    def provider_factory(*args: object, **kwargs: object) -> Provider:
        providers.append(Provider())
        return providers[-1]

    monkeypatch.setattr("agent_manager.api.app.LangGraphEngine", lambda *a, **kw: engine)
    monkeypatch.setattr("agent_manager.composition.WhatsAppSender", provider_factory)
    # Startup replaces logging handlers; keep pytest's capture in place.
    monkeypatch.setattr("agent_manager.api.app.configure_logging", lambda: None)
    caplog.set_level(logging.INFO, logger="agent_manager.application.channels.worker")

    @contextmanager
    def deployment(values: dict) -> Iterator[TestClient]:
        app = create_app(str(CONFIG), Settings.from_values(database_url=url, **values))
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            yield client

    # Intake only: the worker is paused, so the accepted message waits durably.
    with deployment(settings_values()) as client:
        post(client, payload(change(message("question", text={"body": "hello"}))))
    assert [row.state for row in saved_rows(url, ChannelInboxRow)] == ["queued"]
    assert engine.prompts == [] and providers == []

    # Restart with the worker and sender: answered once, then a signed callback lands.
    full = outbound_values(extra_whatsapp_status_log_seconds=0.05)
    with deployment(full) as client:
        wait_until(lambda: providers[0].calls == [(SENDER, "answer:hello")])
        delivered = {
            "id": "wamid.reply-1",
            "recipient_id": SENDER,
            "status": "delivered",
            "timestamp": str(int(datetime.now(UTC).timestamp())),
        }
        post(client, payload(change(statuses=[delivered])))
        wait_until(
            lambda: (
                [part.state for part in saved_rows(url, ChannelDeliveryPartRow)] == ["delivered"]
            )
        )
    assert providers[0].closed

    # A further restart finds nothing to redo.
    with deployment(full):
        time.sleep(0.3)
    assert providers[1].calls == []
    assert engine.prompts == ["hello"]
    assert [part.state for part in saved_rows(url, ChannelDeliveryPartRow)] == ["delivered"]

    status_lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("channel status:")
    ]
    assert status_lines
    for private in (SENDER, "hello", "answer:hello", "wamid."):
        assert not any(private in line for line in status_lines)


EXAMPLE = Path(__file__).parents[2] / "examples/whatsapp-pilot"


def test_pilot_example_config_passes_startup_validation() -> None:
    spec = YAMLParser().parse(str(EXAMPLE / "agents.yml"))
    assert SystemSpecValidator().validate(spec, EXAMPLE) == []


async def test_pilot_allowlist_resolver_links_revokes_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    senders = tmp_path / "senders.json"
    senders.write_text((EXAMPLE / "senders.example.json").read_text(), encoding="utf-8")
    monkeypatch.setenv("WHATSAPP_PILOT_SENDERS_FILE", str(senders))
    monkeypatch.setenv("EXTRA_WHATSAPP_ORGANIZATION_ID", "org")
    # The manager adds the config directory to sys.path at startup.
    monkeypatch.syspath_prepend(str(EXAMPLE))
    resolver = load_channel_identity_resolver("whatsapp_identity:AllowlistIdentityResolver")
    listed = ChannelAddress("whatsapp", "system", "15550001111")

    principal = await resolver.resolve(listed)
    assert principal is not None
    assert (principal.external_id, principal.organization_id, principal.roles) == (
        "pilot-user-1",
        "org",
        ("employee",),
    )
    assert await resolver.resolve(ChannelAddress("whatsapp", "system", "15559999999")) is None

    for malformed in (
        '{"user_id": "u", "roles": "employee"}',
        '{"user_id": " "}',
        '{"user_id": 7}',
    ):
        senders.write_text(f'{{"15550001111": {malformed}}}', encoding="utf-8")
        assert await resolver.resolve(listed) is None

    senders.write_text("{}", encoding="utf-8")
    assert await resolver.resolve(listed) is None

    senders.write_text("not json", encoding="utf-8")
    with pytest.raises(ChannelIdentityUnavailable):
        await resolver.resolve(listed)
