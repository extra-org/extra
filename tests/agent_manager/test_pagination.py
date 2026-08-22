from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

import agent_manager.infrastructure.persistence.tables  # noqa: F401
from agent_manager.application import ConversationService
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.sql_repository import (
    SqlRepository,
    decode_cursor,
    encode_cursor,
)
from tests.agent_manager.conftest import RecordingEngine, bearer, build_test_app


@pytest.fixture
def client() -> TestClient:
    app = build_test_app(ConversationService(RecordingEngine(), MemoryRepository()))
    return TestClient(app, headers=bearer("default-user"))


def test_cursor_encode_decode_round_trip() -> None:
    now = datetime.now(UTC)
    cursor = encode_cursor(now, "sess-123")
    decoded_t, decoded_id = decode_cursor(cursor)

    assert decoded_t == now
    assert decoded_id == "sess-123"


def test_cursor_encode_decode_with_null_timestamp() -> None:
    cursor = encode_cursor(None, "sess-null")
    decoded_t, decoded_id = decode_cursor(cursor)

    assert decoded_t is None
    assert decoded_id == "sess-null"


def test_invalid_cursor_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        decode_cursor("not-a-valid-cursor!")


@pytest.mark.asyncio
async def test_sql_repository_pagination_and_ordering(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test_pag.db'}"
    engine = create_db_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(engine)

    repo = SqlRepository(sessions)
    user_id = "user-pag-1"
    await repo.upsert_user(user_id)

    base_time = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)

    await repo.create_session("sess-1", user_id=user_id, title="Session 1")
    await repo.create_session("sess-2", user_id=user_id, title="Session 2")
    await repo.create_session("sess-3", user_id=user_id, title="Session 3")
    await repo.create_session("sess-4", user_id=user_id, title="Session 4")
    await repo.create_session("sess-5", user_id=user_id, title="Session 5")

    async with sessions() as session:
        from agent_manager.infrastructure.persistence.tables import ConversationSessionRow

        r1 = await session.get(ConversationSessionRow, "sess-1")
        assert r1 is not None
        r1.last_message_at = base_time + timedelta(hours=2)

        r2 = await session.get(ConversationSessionRow, "sess-2")
        assert r2 is not None
        r2.last_message_at = base_time + timedelta(hours=1)

        r3 = await session.get(ConversationSessionRow, "sess-3")
        assert r3 is not None
        r3.last_message_at = base_time + timedelta(hours=1)

        await session.commit()

    # Page 1: limit 2
    p1 = await repo.list_sessions(user_id, limit=2)
    assert [s.session_id for s in p1.sessions] == ["sess-1", "sess-3"]
    assert p1.next_cursor is not None

    # Page 2: limit 2
    p2 = await repo.list_sessions(user_id, limit=2, cursor=p1.next_cursor)
    assert [s.session_id for s in p2.sessions] == ["sess-2", "sess-5"]
    assert p2.next_cursor is not None

    # Page 3: limit 2
    p3 = await repo.list_sessions(user_id, limit=2, cursor=p2.next_cursor)
    assert [s.session_id for s in p3.sessions] == ["sess-4"]
    assert p3.next_cursor is None

    await engine.dispose()


def test_api_conversations_pagination_endpoint(client: TestClient) -> None:
    u1 = bearer("user-api-pag")
    c1 = client.post("/conversations", headers=u1).json()["conversation_id"]
    c2 = client.post("/conversations", headers=u1).json()["conversation_id"]
    c3 = client.post("/conversations", headers=u1).json()["conversation_id"]

    res1 = client.get("/conversations?limit=2", headers=u1).json()
    assert len(res1["items"]) == 2
    assert res1["next_cursor"] is not None

    cursor_q = quote(res1["next_cursor"])
    res2 = client.get(f"/conversations?limit=2&cursor={cursor_q}", headers=u1).json()
    assert len(res2["items"]) == 1
    assert res2["next_cursor"] is None

    fetched_ids = [item["conversation_id"] for item in res1["items"] + res2["items"]]
    assert set(fetched_ids) == {c1, c2, c3}
