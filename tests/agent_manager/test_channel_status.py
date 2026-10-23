"""Operational status: bounded counts and ages, logged without message identifiers."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlmodel import SQLModel

from agent_manager.application.channels.processing import ChannelTurnProcessor
from agent_manager.application.channels.worker import ChannelWorker
from agent_manager.config import Settings
from agent_manager.domain.channels.repository import ChannelRepository
from agent_manager.domain.channels.status import ChannelStatus, ChannelStatusReader
from agent_manager.infrastructure.persistence.channels.status import SqlChannelStatusReader
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryBatchRow,
    ChannelDeliveryPartRow,
    ChannelInboxRow,
)
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from tests.agent_manager.test_channel_worker import NOW, OPTIONS, Ownership

SENDER = "+15550001111"


@pytest.fixture
async def reader(tmp_path: Path) -> AsyncIterator[tuple[SqlChannelStatusReader, Any]]:
    database = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'status.db'}")
    async with database.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(database)
    try:
        yield SqlChannelStatusReader(sessions), sessions
    finally:
        await database.dispose()


def inbox(event_id: str, state: str, age: int, *, connection: str = "c") -> ChannelInboxRow:
    return ChannelInboxRow(
        event_id=event_id,
        connection_id=connection,
        system_id="s",
        sender_id=SENDER,
        provider_message_id=f"wamid.{event_id}",
        kind="text",
        content="private message text",
        received_at=NOW - timedelta(seconds=age),
        state=state,
    )


def delivery(
    event_id: str, state: str, sequence: int, *, parts: int = 1, connection: str = "c"
) -> list[ChannelDeliveryBatchRow | ChannelDeliveryPartRow]:
    return [
        ChannelDeliveryBatchRow(
            event_id=event_id,
            connection_id=connection,
            system_id="s",
            sender_id=SENDER,
            sequence=sequence,
            state=state,
        ),
        *(
            ChannelDeliveryPartRow(
                delivery_id=f"d-{event_id}-{index}",
                event_id=event_id,
                connection_id=connection,
                part_index=index,
                text="private reply text",
                state=state,
            )
            for index in range(parts)
        ),
    ]


async def test_reader_counts_attention_states_and_ages_for_one_connection(reader):
    status_reader, sessions = reader
    async with sessions() as session, session.begin():
        session.add_all(
            [
                inbox("queued", "queued", 100),
                inbox("preparing", "preparing", 50),
                inbox("answered", "answered", 30),
                inbox("unknown", "done", 1000),
                inbox("failed-send", "done", 900),
                inbox("blocked", "done", 800),
                inbox("review", "needs_review", 700),
                inbox("failed", "failed", 600),
                inbox("other", "queued", 5000, connection="other"),
            ]
        )
    async with sessions() as session, session.begin():
        session.add_all(
            [
                # Pending work counts parts; outcomes count replies.
                *delivery("answered", "ready", 3, parts=2),
                *delivery("unknown", "delivery_unknown", 4),
                *delivery("failed-send", "failed", 5, parts=2),
                *delivery("blocked", "blocked_window", 6),
                *delivery("other", "ready", 1, connection="other"),
            ]
        )

    status = await status_reader.read("c", "s", NOW)

    assert status == ChannelStatus(
        inbox_queued=1,
        inbox_running=1,
        inbox_answered=1,
        inbox_needs_review=1,
        inbox_failed=1,
        inbox_oldest_seconds=100.0,
        outbox_pending=2,
        outbox_oldest_seconds=30.0,
        delivery_unknown=1,
        delivery_failed=1,
        delivery_blocked=1,
    )


async def test_empty_connection_reports_zero_counts_and_no_ages(reader):
    status_reader, _ = reader
    status = await status_reader.read("c", "s", NOW)
    assert status == ChannelStatus(0, 0, 0, 0, 0, None, 0, None, 0, 0, 0)


class IdleRepository:
    async def list_active(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def claim_next(self, *args: Any, **kwargs: Any) -> None:
        return None


class FixedReader(ChannelStatusReader):
    def __init__(self, status: ChannelStatus | Exception) -> None:
        self.status = status
        self.reads = 0

    async def read(self, connection_id: str, system_id: str, now: datetime) -> ChannelStatus:
        self.reads += 1
        if isinstance(self.status, Exception):
            raise self.status
        return self.status


async def run_worker(reader: ChannelStatusReader, *, status_seconds: float) -> bool:
    worker = ChannelWorker(
        cast(ChannelRepository, IdleRepository()),
        cast(ChannelTurnProcessor, None),
        Ownership(),
        replace(OPTIONS, status_seconds=status_seconds),
        status=reader,
    )
    async with worker:
        await asyncio.sleep(0.15)
        return worker.running


STATUS = ChannelStatus(2, 1, 0, 1, 0, 42.9, 3, None, 1, 0, 2)


async def test_worker_logs_one_bounded_status_line_per_interval(caplog):
    caplog.set_level(logging.INFO, logger="agent_manager.application.channels.worker")
    reader = FixedReader(STATUS)

    assert await run_worker(reader, status_seconds=60)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("channel status:")]
    assert lines == [
        "channel status: connection=connection inbox_queued=2 inbox_running=1 "
        "inbox_answered=0 inbox_oldest_s=42 needs_review=1 inbox_failed=0 "
        "outbox_pending=3 outbox_oldest_s=none delivery_unknown=1 "
        "delivery_failed=0 delivery_blocked=2"
    ]
    assert reader.reads == 1


async def test_short_interval_logs_repeatedly(caplog):
    caplog.set_level(logging.INFO, logger="agent_manager.application.channels.worker")
    reader = FixedReader(STATUS)
    assert await run_worker(reader, status_seconds=0.02)
    assert reader.reads >= 2


async def test_status_failure_is_logged_by_type_and_never_stops_the_worker(caplog):
    caplog.set_level(logging.INFO, logger="agent_manager.application.channels.worker")
    reader = FixedReader(RuntimeError(f"database said {SENDER}"))

    assert await run_worker(reader, status_seconds=60)

    assert "channel status unavailable: type=RuntimeError" in caplog.text
    assert SENDER not in caplog.text


class HangingReader(ChannelStatusReader):
    async def read(self, connection_id: str, system_id: str, now: datetime) -> ChannelStatus:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_hanging_status_read_neither_stalls_the_worker_nor_its_shutdown(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="agent_manager.application.channels.worker")
    monkeypatch.setattr(
        "agent_manager.application.channels.worker.STATUS_READ_TIMEOUT_SECONDS", 0.05
    )
    started = time.monotonic()

    assert await run_worker(HangingReader(), status_seconds=60)

    # Without a bound, shutdown waits drain_seconds + 1 and then cancels.
    assert time.monotonic() - started < OPTIONS.drain_seconds + 1
    assert "channel status unavailable: type=TimeoutError" in caplog.text


@pytest.mark.parametrize("seconds", [0, -1, 3601, float("inf")])
def test_status_interval_is_bounded(seconds):
    with pytest.raises(ValueError):
        replace(OPTIONS, status_seconds=seconds)
    with pytest.raises(ValidationError):
        Settings.from_values(extra_whatsapp_status_log_seconds=seconds)
