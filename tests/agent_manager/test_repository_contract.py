"""One contract, run against every Repository implementation.

If a backend diverges from the port's expected behavior, these fail — which is
the proof that swapping SQLite/Postgres/in-memory is safe.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel, select

import agent_manager.infrastructure.persistence.tables  # noqa: F401  (register tables)
from agent_manager.domain import (
    ConversationMessage,
    InvalidCursorError,
    PageRequest,
    Repository,
    Role,
)
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository
from agent_manager.infrastructure.persistence.tables import (
    ConversationMessageRow,
    ConversationSnapshotRow,
)


@asynccontextmanager
async def _sql_database() -> AsyncIterator[AsyncEngine]:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(params=["memory", "sql"])
async def repo(request: pytest.FixtureRequest) -> AsyncIterator[Repository]:
    if request.param == "memory":
        yield MemoryRepository()
    else:
        async with _sql_database() as engine:
            yield SqlRepository(session_factory(engine))


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
    assert (await repo.list_sessions("bob")).items == []


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
    assert (await repo.list_sessions("bob")).items == []


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


async def test_run_usage_is_cumulative_idempotent_and_isolated(repo: Repository) -> None:
    await repo.create_session("charged")
    await repo.create_session("other")
    await repo.record_run_token_usage("charged", "cancelled", 5)
    await repo.record_run_token_usage("charged", "resumed", 3)
    await repo.record_run_token_usage("charged", "resumed", 10)
    await repo.record_run_token_usage("charged", "resumed", 10)
    await repo.record_run_token_usage("charged", "resumed", 4)
    await repo.record_run_token_usage("other", "separate", 100)

    assert await repo.get_token_usage("charged") == 15
    assert await repo.get_token_usage("other") == 100


async def test_run_usage_does_not_double_count_answers_or_lose_legacy_usage(
    repo: Repository,
) -> None:
    await repo.create_session("charged")
    for message in (
        _message("charged", Role.ASSISTANT, "legacy", run_id="old", input_tokens=7),
        _message("charged", Role.ASSISTANT, "resumed", run_id="new", input_tokens=10),
        _message("charged", Role.ASSISTANT, "no run", run_id=None, input_tokens=2),
    ):
        await repo.append_message(message)
    await repo.record_run_token_usage("charged", "new", 10)
    await repo.record_run_token_usage("charged", "old", 3)
    await repo.record_run_token_usage("charged", "cancelled", 5)

    assert await repo.get_token_usage("charged") == 24  # 7 + 10 + 2 + 5


async def test_concurrent_usage_observations_keep_the_largest_total(repo: Repository) -> None:
    await repo.create_session("charged")
    await asyncio.gather(
        *(repo.record_run_token_usage("charged", "run", total) for total in (5, 15, 10, 5))
    )

    assert await repo.get_token_usage("charged") == 15


async def test_usage_projection_includes_uncharged_runs_on_inactive_branches(
    repo: Repository,
) -> None:
    await repo.create_session("charged")
    await repo.create_session("other")
    now = datetime.now(UTC)
    original = ConversationMessage(
        "original", "charged", Role.USER, "original", now, run_id="missed-charge"
    )
    edited = ConversationMessage("edited", "charged", Role.USER, "edited", now, run_id="active-run")
    await repo.append_message_if_head(original, None)
    assert await repo.append_message_if_head(edited, "original")
    await repo.record_run_token_usage("charged", "active-run", 5)
    await repo.record_run_token_usage("other", "foreign-run", 100)

    assert await repo.list_conversation_messages("charged") == [edited]
    assert await repo.get_token_usage_by_run("charged") == {
        "missed-charge": 0,
        "active-run": 5,
    }


async def test_negative_usage_cannot_refund_a_run(repo: Repository) -> None:
    await repo.create_session("charged")
    await repo.record_run_token_usage("charged", "run", 5)

    with pytest.raises(ValueError, match="non-negative"):
        await repo.record_run_token_usage("charged", "run", -1)

    assert await repo.get_token_usage("charged") == 5


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
    async with _sql_database() as engine:
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
    async with _sql_database() as engine:
        repo = SqlRepository(session_factory(engine))

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
    assert [s.session_id for s in (await repo.list_sessions("ext:alice")).items] == ["pre-login"]
    assert (await repo.list_sessions("anon:v1")).items == []

    visitor = await repo.get_user("anon:v1")
    assert visitor is not None and visitor.linked_to_user_id == "ext:alice"

    # Spent: a replayed pass moves nothing, whoever presents it.
    await repo.upsert_user("ext:bob")
    assert await repo.link_anonymous_user("anon:v1", "ext:bob") == 0
    assert (await repo.list_sessions("ext:bob")).items == []


async def test_linking_an_unknown_visitor_is_a_no_op(repo: Repository) -> None:
    await repo.upsert_user("ext:alice")
    assert await repo.link_anonymous_user("anon:never-seen", "ext:alice") == 0


async def test_pagination_contract(repo: Repository) -> None:
    """Comprehensive contract tests for repository pagination."""
    user_id = "paginated_user"
    await repo.upsert_user(user_id)

    base_time = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    s1 = await repo.create_session("s1", user_id=user_id)
    s2 = await repo.create_session("s2", user_id=user_id)
    await repo.create_session("s3", user_id=user_id)
    await repo.create_session("s4", user_id=user_id)
    await repo.create_session("s5", user_id=user_id)

    # If repo is SqlRepository, set created_at explicitly so created_at ordering is deterministic
    if isinstance(repo, SqlRepository):
        async with repo._sessions() as session:
            from agent_manager.infrastructure.persistence.tables import ConversationSessionRow

            r1 = await session.get(ConversationSessionRow, "s1")
            if r1:
                r1.created_at = base_time
            r2 = await session.get(ConversationSessionRow, "s2")
            if r2:
                r2.created_at = base_time + timedelta(minutes=10)
            r3 = await session.get(ConversationSessionRow, "s3")
            if r3:
                r3.created_at = base_time
            r4 = await session.get(ConversationSessionRow, "s4")
            if r4:
                r4.created_at = base_time
            r5 = await session.get(ConversationSessionRow, "s5")
            if r5:
                r5.created_at = base_time
            await session.commit()
    elif isinstance(repo, MemoryRepository):
        from dataclasses import replace

        repo._sessions["s1"] = replace(s1, created_at=base_time)
        repo._sessions["s2"] = replace(s2, created_at=base_time + timedelta(minutes=10))

    # Append messages to s3, s4, s5 (s4 and s5 share the same last_message_at timestamp)
    await repo.append_message(
        ConversationMessage(
            message_id="m3",
            session_id="s3",
            role=Role.USER,
            content="m3",
            created_at=base_time + timedelta(hours=1),
        )
    )
    await repo.append_message(
        ConversationMessage(
            message_id="m4",
            session_id="s4",
            role=Role.USER,
            content="m4",
            created_at=base_time + timedelta(hours=2),
        )
    )
    await repo.append_message(
        ConversationMessage(
            message_id="m5",
            session_id="s5",
            role=Role.USER,
            content="m5",
            created_at=base_time + timedelta(hours=2),
        )
    )

    # Page 1: limit 2 -> ["s5", "s4"]
    page1 = await repo.list_sessions(user_id, page=PageRequest(limit=2))
    assert [s.session_id for s in page1.items] == ["s5", "s4"]
    assert page1.next_cursor is not None

    # Page 2: limit 2 -> ["s3", "s2"]
    page2 = await repo.list_sessions(user_id, page=PageRequest(limit=2, cursor=page1.next_cursor))
    assert [s.session_id for s in page2.items] == ["s3", "s2"]
    assert page2.next_cursor is not None

    # Page 3: limit 2 -> ["s1"]
    page3 = await repo.list_sessions(user_id, page=PageRequest(limit=2, cursor=page2.next_cursor))
    assert [s.session_id for s in page3.items] == ["s1"]
    assert page3.next_cursor is None

    all_ids = [s.session_id for s in [*page1.items, *page2.items, *page3.items]]
    assert all_ids == ["s5", "s4", "s3", "s2", "s1"]

    # Page boundary landing exactly on limit
    page_exact = await repo.list_sessions(user_id, page=PageRequest(limit=5))
    assert len(page_exact.items) == 5
    assert page_exact.next_cursor is None

    # Malformed cursor raises InvalidCursorError
    with pytest.raises(InvalidCursorError):
        await repo.list_sessions(user_id, page=PageRequest(cursor="invalid_garbage_token"))
