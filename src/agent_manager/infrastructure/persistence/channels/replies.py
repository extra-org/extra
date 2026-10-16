"""Atomically stage one immutable reply; provider sending is a separate concern."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.models import (
    ChannelClaimConflict,
    ChannelMessageConflict,
    InboxState,
    StagedReply,
    bounded_identifier,
    bounded_text,
    stable_id,
)
from agent_manager.domain.pagination import ensure_utc
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelInboxRow,
    ChannelOutboxRow,
)


async def stage_reply(
    sessions: async_sessionmaker[AsyncSession],
    event_id: str,
    token: str,
    response_id: str,
    parts: Sequence[str],
) -> tuple[StagedReply, ...]:
    bounded_identifier(response_id, limit=64)
    parts = tuple(parts)
    if not 1 <= len(parts) <= 128:
        raise ValueError("reply requires 1 to 128 parts")
    for text in parts:
        bounded_text(text)
        if not text.strip():
            raise ValueError("reply parts must not be empty")
    async with sessions() as session, session.begin():
        rows = await session.exec(
            select(ChannelInboxRow)
            .where(col(ChannelInboxRow.event_id) == event_id)
            .with_for_update()
        )
        inbox = rows.first()
        if inbox is None or not token or inbox.claim_token != token:
            raise ChannelClaimConflict("invalid reply work claim")
        changed = await session.exec(
            update(ChannelInboxRow)
            .where(
                col(ChannelInboxRow.event_id) == event_id,
                col(ChannelInboxRow.state) == InboxState.RUNNING.value,
                col(ChannelInboxRow.claim_token) == token,
            )
            .values(state=InboxState.ANSWERED.value, response_id=response_id)
            .returning(col(ChannelInboxRow.event_id))
        )
        if changed.scalar_one_or_none() is None:
            await session.refresh(inbox)
            if inbox.state not in {InboxState.ANSWERED, InboxState.DONE}:
                raise ChannelClaimConflict("reply requires running or answered work")
            existing = await _list(session, event_id)
            if inbox.response_id != response_id or tuple(part.text for part in existing) != parts:
                raise ChannelMessageConflict("staged reply cannot be replaced")
            return existing
        now = datetime.now(UTC)
        for index, text in enumerate(parts):
            session.add(
                ChannelOutboxRow(
                    reply_id=stable_id("reply", event_id, response_id, str(index)),
                    event_id=event_id,
                    response_id=response_id,
                    part_index=index,
                    text=text,
                    created_at=now,
                )
            )
        await session.flush()
        await session.exec(
            update(ChannelBindingRow)
            .where(
                and_(
                    col(ChannelBindingRow.connection_id) == inbox.connection_id,
                    col(ChannelBindingRow.system_id) == inbox.system_id,
                    col(ChannelBindingRow.sender_id) == inbox.sender_id,
                    col(ChannelBindingRow.active_event_id) == event_id,
                )
            )
            .values(active_event_id=None)
        )
        return await _list(session, event_id)


async def list_replies(
    sessions: async_sessionmaker[AsyncSession],
    event_id: str,
) -> tuple[StagedReply, ...]:
    async with sessions() as session:
        return await _list(session, event_id)


async def _list(session: AsyncSession, event_id: str) -> tuple[StagedReply, ...]:
    rows = await session.exec(
        select(ChannelOutboxRow)
        .where(col(ChannelOutboxRow.event_id) == event_id)
        .order_by(col(ChannelOutboxRow.part_index))
        .limit(128)
    )
    return tuple(
        StagedReply(
            row.reply_id,
            row.event_id,
            row.response_id,
            row.part_index,
            row.text,
            ensure_utc(row.created_at) or row.created_at,
        )
        for row in rows.all()
    )
