"""One contract, run against every Repository implementation.

If a backend diverges from the port's expected behavior, these fail — which is
the proof that swapping SQLite/Postgres/in-memory is safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import SQLModel, select

import agent_manager.infrastructure.persistence.tables  # noqa: F401  (register tables)
from agent_manager.domain import ConversationMessage, Repository, Role
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from agent_manager.infrastructure.persistence.tables import (
    ConversationMessageRow,
    ConversationSnapshotRow,
)


async def _memory() -> Repository:
    return MemoryRepository()


async def _sql() -> Repository:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return SqlRepository(session_factory(engine))


@pytest.fixture(params=[_memory, _sql], ids=["memory", "sql"])
async def repo(request: pytest.FixtureRequest) -> Repository:
    return await request.param()


async def test_create_and_exists(repo: Repository) -> None:
    cid = await repo.create_conversation()
    assert await repo.conversation_exists(cid)
    assert not await repo.conversation_exists("nope")


async def test_create_session_never_reassigns_an_existing_owner(repo: Repository) -> None:
    """The backends used to disagree here, so this runs against both: memory
    returned the existing session untouched while SQL overwrote `user_id`."""
    await repo.create_session("shared-id", user_id="alice")

    session = await repo.create_session("shared-id", user_id="bob")

    assert session.user_id == "alice"
    stored = await repo.get_session("shared-id")
    assert stored is not None
    assert stored.user_id == "alice"
    assert (await repo.list_sessions("bob")).sessions == []


async def test_create_session_writes_nothing_when_the_id_is_taken(repo: Repository) -> None:
    """Creation fields describe a birth, so a taken id is left entirely alone —
    a rejected create must not rebind a live session to another system."""
    expiry = datetime.now(UTC) + timedelta(days=1)
    original = await repo.create_session(
        "shared-id",
        user_id="alice",
        system_name="system-a",
        config_path="/a/agents.yml",
        title="Alice's thread",
        metadata={"source": "a"},
        expires_at=expiry,
    )

    returned = await repo.create_session(
        "shared-id",
        user_id="bob",
        system_name="system-b",
        config_path="/b/agents.yml",
        title="Bob's thread",
        metadata={"source": "b"},
        expires_at=expiry + timedelta(days=7),
    )

    assert returned == original
    assert await repo.get_session("shared-id") == original


async def test_appending_a_message_never_claims_the_conversation(repo: Repository) -> None:
    await repo.create_session("owned", user_id="alice")
    await repo.create_session("unowned")

    for sid in ("owned", "unowned"):
        await repo.append_message(
            ConversationMessage(
                message_id=uuid4().hex,
                session_id=sid,
                user_id="bob",
                role=Role.USER,
                content="hi",
                created_at=datetime.now(UTC),
            )
        )

    owned = await repo.get_session("owned")
    unowned = await repo.get_session("unowned")
    assert owned is not None and owned.user_id == "alice"
    assert unowned is not None and unowned.user_id is None
    assert (await repo.list_sessions("bob")).sessions == []


async def test_messages_in_insertion_order(repo: Repository) -> None:
    cid = await repo.create_conversation()
    await repo.add_message(cid, Role.USER, "hi")
    await repo.add_message(cid, Role.ASSISTANT, "hello")
    await repo.add_message(cid, Role.USER, "bye")
    msgs = await repo.list_messages(cid)
    assert [(m.role, m.content) for m in msgs] == [
        (Role.USER, "hi"),
        (Role.ASSISTANT, "hello"),
        (Role.USER, "bye"),
    ]


async def test_append_message_if_absent_is_idempotent(repo: Repository) -> None:
    await repo.create_session("sess-1")
    message = ConversationMessage(
        message_id="stable-message",
        session_id="sess-1",
        run_id="run-1",
        role=Role.ASSISTANT,
        content="done",
        created_at=datetime.now(UTC),
    )

    created = await repo.append_message_if_absent(message)
    duplicate = await repo.append_message_if_absent(message)

    assert created is True
    assert duplicate is False
    stored = await repo.list_conversation_messages("sess-1")
    assert stored == [message]


async def test_branch_head_selects_one_immutable_ancestry(repo: Repository) -> None:
    await repo.create_session("branch")
    now = datetime.now(UTC)
    root = ConversationMessage("root", "branch", Role.USER, "U1", now)
    answer = ConversationMessage(
        "answer",
        "branch",
        Role.ASSISTANT,
        "A1",
        now + timedelta(microseconds=1),
        parent_message_id="root",
    )
    original = ConversationMessage(
        "original",
        "branch",
        Role.USER,
        "U2",
        now + timedelta(microseconds=2),
        run_id="run-original",
        parent_message_id="answer",
    )
    for message in (root, answer, original):
        await repo.append_message(message)

    edited = ConversationMessage(
        "edited",
        "branch",
        Role.USER,
        "U2 edited",
        now + timedelta(microseconds=3),
        parent_message_id="answer",
    )
    assert await repo.append_message_if_head(edited, "original") is True
    assert (
        await repo.append_message_if_head(
            ConversationMessage("racer", "branch", Role.USER, "racer", now),
            "original",
        )
        is False
    )

    assert [message.message_id for message in await repo.list_conversation_messages("branch")] == [
        "root",
        "answer",
        "edited",
    ]
    assert await repo.get_message("original") == original
    assert await repo.get_user_message_for_run("branch", "run-original") == original

    late_original_answer = ConversationMessage(
        "late-answer",
        "branch",
        Role.ASSISTANT,
        "late A2",
        now + timedelta(microseconds=4),
        parent_message_id="original",
    )
    assert await repo.append_message_if_absent(late_original_answer) is True
    assert [message.message_id for message in await repo.list_conversation_messages("branch")] == [
        "root",
        "answer",
        "edited",
    ]
    assert await repo.get_message("late-answer") == late_original_answer


async def test_limit_returns_most_recent_oldest_first(repo: Repository) -> None:
    cid = await repo.create_conversation()
    for i in range(5):
        await repo.add_message(cid, Role.USER, f"m{i}")
    assert [m.content for m in await repo.list_messages(cid, limit=2)] == ["m3", "m4"]
    assert await repo.list_messages(cid, limit=0) == []


async def test_limit_counts_only_the_active_branch(repo: Repository) -> None:
    """A limited read walks the selected ancestry, not whatever else the session stores."""
    now = datetime.now(UTC)
    await repo.create_session("branch", user_id="u1")
    await repo.append_message_if_head(
        ConversationMessage("root", "branch", Role.USER, "Q1", now), None
    )
    await repo.append_message_if_head(
        ConversationMessage(
            "answer",
            "branch",
            Role.ASSISTANT,
            "A1",
            now + timedelta(microseconds=1),
            parent_message_id="root",
        ),
        "root",
    )
    await repo.append_message_if_head(
        ConversationMessage(
            "abandoned",
            "branch",
            Role.USER,
            "Q2",
            now + timedelta(microseconds=2),
            parent_message_id="answer",
        ),
        "answer",
    )
    # Edit Q2: a sibling of `abandoned` becomes the head, leaving it stored but inactive.
    await repo.append_message_if_head(
        ConversationMessage(
            "edited",
            "branch",
            Role.USER,
            "Q2 edited",
            now + timedelta(microseconds=3),
            parent_message_id="answer",
        ),
        "abandoned",
    )

    limited = await repo.list_conversation_messages("branch", limit=2)

    assert [message.message_id for message in limited] == ["answer", "edited"]


async def test_same_turn_keeps_order(repo: Repository) -> None:
    cid = await repo.create_conversation()
    await repo.add_message(cid, Role.USER, "q")
    await repo.add_message(cid, Role.ASSISTANT, "a")
    assert [m.role for m in await repo.list_messages(cid)] == [Role.USER, Role.ASSISTANT]


async def test_isolated_per_conversation(repo: Repository) -> None:
    a = await repo.create_conversation()
    b = await repo.create_conversation()
    await repo.add_message(a, Role.USER, "in-a")
    assert await repo.list_messages(b) == []


async def test_create_and_get_user(repo: Repository) -> None:
    user = await repo.upsert_user(
        "u1",
        external_user_id="ext-1",
        username="alice",
        display_name="Alice",
        metadata={"tier": "pro"},
    )

    fetched = await repo.get_user("u1")

    assert fetched == user
    assert fetched is not None
    assert fetched.metadata == {"tier": "pro"}


async def test_create_and_get_session(repo: Repository) -> None:
    await repo.upsert_user("u1")

    session = await repo.create_session(
        "sess-1",
        user_id="u1",
        system_name="system",
        config_path="/tmp/agents.yml",
        title="Support",
        metadata={"source": "test"},
    )

    fetched = await repo.get_session("sess-1")
    assert fetched == session
    assert fetched is not None
    assert fetched.user_id == "u1"
    assert fetched.system_name == "system"


async def test_append_message_updates_cold_and_hot_snapshot(repo: Repository) -> None:
    await repo.create_session("sess-1", user_id="u1")
    message = _message("sess-1", Role.USER, "hello", run_id="run-1", user_id="u1")

    snapshot = await repo.append_message(message, snapshot_ttl_seconds=60)

    cold = await repo.list_conversation_messages("sess-1")
    assert [m.message_id for m in cold] == [message.message_id]
    assert snapshot.session_id == "sess-1"
    assert snapshot.user_id == "u1"
    assert snapshot.message_count == 1
    assert snapshot.last_message_id == message.message_id
    assert snapshot.expires_at is not None


async def test_retrieve_conversation_context_with_bounds(repo: Repository) -> None:
    await repo.create_session("sess-1")
    for i in range(5):
        await repo.append_message(_message("sess-1", Role.USER, f"message-{i}", run_id=f"run-{i}"))

    context = await repo.get_context("sess-1", max_messages=2, max_chars=100)

    assert context.session_id == "sess-1"
    assert context.message_count == 5
    assert [m.content for m in context.messages] == ["message-3", "message-4"]


async def test_get_token_usage_sums_across_messages(repo: Repository) -> None:
    await repo.create_session("sess-1")
    msg1 = _message("sess-1", Role.USER, "hi", run_id="r1")
    msg2 = _message(
        "sess-1", Role.ASSISTANT, "hello", run_id="r1", input_tokens=10, output_tokens=5
    )
    msg3 = _message("sess-1", Role.USER, "bye", run_id="r2")
    msg4 = _message("sess-1", Role.ASSISTANT, "ok", run_id="r2", input_tokens=20, output_tokens=8)
    for msg in (msg1, msg2, msg3, msg4):
        await repo.append_message(msg)

    assert await repo.get_token_usage("sess-1") == 43  # 10+5+20+8


async def test_delete_expired_snapshots_and_rebuild(repo: Repository) -> None:
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "hello"), snapshot_ttl_seconds=1)
    deleted = await repo.delete_expired_snapshots(datetime.now(UTC) + timedelta(seconds=2))

    assert deleted == 1
    assert await repo.get_snapshot("sess-1") is None

    rebuilt = await repo.rebuild_snapshot("sess-1")
    assert rebuilt is not None
    assert rebuilt.message_count == 1


async def test_same_session_multiple_appends_one_snapshot_row(repo: Repository) -> None:
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "a", run_id="run-1"))
    await repo.append_message(_message("sess-1", Role.ASSISTANT, "b", run_id="run-2"))

    snapshot = await repo.get_snapshot("sess-1")

    assert snapshot is not None
    assert snapshot.message_count == 2
    assert snapshot.session_id == "sess-1"


async def test_different_run_ids_can_share_one_session(repo: Repository) -> None:
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "a", run_id="run-1"))
    await repo.append_message(_message("sess-1", Role.USER, "b", run_id="run-2"))

    messages = await repo.list_conversation_messages("sess-1")

    assert [m.run_id for m in messages] == ["run-1", "run-2"]


async def test_sql_schema_has_cold_and_snapshot_rows() -> None:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(engine)
    repo = SqlRepository(sessions)
    await repo.create_session("sess-1")
    await repo.append_message(_message("sess-1", Role.USER, "hello"))

    async with sessions() as session:
        cold = (await session.exec(select(ConversationMessageRow))).all()
        snapshots = (await session.exec(select(ConversationSnapshotRow))).all()

    assert len(cold) == 1
    assert len(snapshots) == 1


def test_legacy_conversations_and_messages_tables_are_gone() -> None:
    """Regression guard for the 0001-era schema removed in migration 0003.

    `ConversationRow`/`MessageRow` were write-only compatibility shims that
    nothing ever read back; `SqlRepository` now relies solely on the
    `Repository` base class's alias methods over the current schema. If
    either name (or the `conversations`/`messages` tables) reappears, this
    should fail loudly rather than silently reintroducing schema confusion.
    """
    import agent_manager.infrastructure.persistence.tables as tables_module

    assert not hasattr(tables_module, "ConversationRow")
    assert not hasattr(tables_module, "MessageRow")
    assert "conversations" not in SQLModel.metadata.tables
    assert "messages" not in SQLModel.metadata.tables


async def test_sql_repository_does_not_write_legacy_tables() -> None:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sessions = session_factory(engine)
    repo = SqlRepository(sessions)

    cid = await repo.create_conversation()
    await repo.add_message(cid, Role.USER, "hi")

    assert await repo.conversation_exists(cid)
    assert [m.content for m in await repo.list_messages(cid)] == ["hi"]


def _message(
    session_id: str,
    role: Role,
    content: str,
    *,
    run_id: str | None = None,
    user_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ConversationMessage:
    return ConversationMessage(
        message_id=uuid4().hex,
        session_id=session_id,
        run_id=run_id,
        user_id=user_id,
        role=role,
        content=content,
        created_at=datetime.now(UTC),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def test_linking_a_visitor_moves_their_sessions_once(repo: Repository) -> None:
    await repo.upsert_user("anon:v1")
    await repo.upsert_user("ext:alice")
    await repo.create_session("pre-login", user_id="anon:v1")

    assert await repo.link_anonymous_user("anon:v1", "ext:alice") == 1
    moved = await repo.get_session("pre-login")
    assert moved is not None and moved.user_id == "ext:alice"
    assert [s.session_id for s in (await repo.list_sessions("ext:alice")).sessions] == ["pre-login"]
    assert (await repo.list_sessions("anon:v1")).sessions == []

    visitor = await repo.get_user("anon:v1")
    assert visitor is not None and visitor.linked_to_user_id == "ext:alice"

    # Spent: a replayed pass moves nothing, whoever presents it.
    await repo.upsert_user("ext:bob")
    assert await repo.link_anonymous_user("anon:v1", "ext:bob") == 0
    assert (await repo.list_sessions("ext:bob")).sessions == []


async def test_linking_an_unknown_visitor_is_a_no_op(repo: Repository) -> None:
    await repo.upsert_user("ext:alice")
    assert await repo.link_anonymous_user("anon:never-seen", "ext:alice") == 0
