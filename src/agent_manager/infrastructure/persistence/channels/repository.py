"""SQL inbox/binding transactions; claims never expire into duplicate work."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from typing import cast

from sqlalchemy import and_, or_, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.models import (
    ChannelAddress,
    ChannelBinding,
    ChannelClaimConflict,
    ChannelOwnerConflict,
    InboundMessage,
    InboxItem,
    InboxState,
    MessageKind,
    StagedReply,
    bounded_identifier,
)
from agent_manager.domain.channels.repository import ChannelRepository
from agent_manager.domain.pagination import ensure_utc
from agent_manager.infrastructure.persistence.channels.intake import ingest_in_session
from agent_manager.infrastructure.persistence.channels.replies import list_replies, stage_reply
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelBindingRow,
    ChannelInboxRow,
)

_TERMINAL = frozenset({InboxState.DONE, InboxState.FAILED, InboxState.DENIED})


class SqlChannelRepository(ChannelRepository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ingest(self, message: InboundMessage) -> InboxItem:
        async with self._sessions() as session, session.begin():
            return _item(await ingest_in_session(session, message))

    async def get_inbound(self, event_id: str) -> InboxItem | None:
        async with self._sessions() as session:
            row = await _inbound(session, event_id)
            return _item(row) if row is not None else None

    async def get_binding(self, address: ChannelAddress) -> ChannelBinding | None:
        async with self._sessions() as session:
            row = await session.get(
                ChannelBindingRow, (address.connection_id, address.system_id, address.sender_id)
            )
            return _binding(row) if row is not None else None

    async def list_active(
        self, connection_id: str, system_id: str, *, after_sequence: int = 0, limit: int = 32
    ) -> tuple[InboxItem, ...]:
        if after_sequence < 0 or not 1 <= limit <= 100:
            raise ValueError("invalid channel recovery page bounds")
        async with self._sessions() as session:
            rows = await session.exec(
                select(ChannelInboxRow)
                .join(ChannelBindingRow, _binding_condition(ChannelInboxRow))
                .where(
                    col(ChannelInboxRow.connection_id) == connection_id,
                    col(ChannelInboxRow.system_id) == system_id,
                    col(ChannelInboxRow.sequence) > after_sequence,
                    col(ChannelBindingRow.active_event_id) == col(ChannelInboxRow.event_id),
                    col(ChannelInboxRow.state).in_(
                        (
                            InboxState.PREPARING.value,
                            InboxState.PREPARED.value,
                            InboxState.RUNNING.value,
                        )
                    ),
                )
                .order_by(col(ChannelInboxRow.sequence))
                .limit(limit)
            )
            return tuple(_item(row) for row in rows.all())

    async def claim_next(self, connection_id: str, system_id: str) -> InboxItem | None:
        async with self._sessions() as session, session.begin():
            pending = await session.exec(
                select(ChannelInboxRow)
                .join(ChannelBindingRow, _binding_condition(ChannelInboxRow))
                .where(
                    col(ChannelInboxRow.connection_id) == connection_id,
                    col(ChannelInboxRow.system_id) == system_id,
                    col(ChannelInboxRow.state) == InboxState.QUEUED.value,
                    col(ChannelBindingRow.active_event_id).is_(None),
                )
                .order_by(col(ChannelInboxRow.sequence))
                .limit(32)
            )
            for row in pending.all():
                claimed = await session.exec(
                    update(ChannelBindingRow)
                    .where(
                        _binding_condition(row), col(ChannelBindingRow.active_event_id).is_(None)
                    )
                    .values(active_event_id=row.event_id)
                    .returning(col(ChannelBindingRow.connection_id))
                )
                if claimed.scalar_one_or_none() is None:
                    continue
                changed = await session.exec(
                    update(ChannelInboxRow)
                    .where(
                        col(ChannelInboxRow.event_id) == row.event_id,
                        col(ChannelInboxRow.state) == InboxState.QUEUED.value,
                    )
                    .values(state=InboxState.PREPARING.value, claim_token=secrets.token_hex(16))
                    .returning(ChannelInboxRow)
                )
                claimed_row = changed.scalar_one_or_none()
                if claimed_row is None:
                    raise ChannelClaimConflict("inbox changed while claiming its sender")
                return _item(claimed_row)
        return None

    async def bind_conversation(
        self, event_id: str, token: str, user_id: str, organization_id: str, conversation_id: str
    ) -> ChannelBinding:
        for value in (user_id, conversation_id):
            bounded_identifier(value, limit=64)
        bounded_identifier(organization_id)
        async with self._sessions() as session, session.begin():
            row = await _claimed(session, event_id, token)
            if row.state not in {InboxState.PREPARING, InboxState.PREPARED}:
                raise ChannelClaimConflict("binding requires a preparing or prepared turn")
            changed = await session.exec(
                update(ChannelBindingRow)
                .where(
                    _binding_condition(row),
                    col(ChannelBindingRow.active_event_id) == event_id,
                    or_(
                        col(ChannelBindingRow.user_id).is_(None),
                        col(ChannelBindingRow.user_id) == user_id,
                    ),
                    or_(
                        col(ChannelBindingRow.organization_id).is_(None),
                        col(ChannelBindingRow.organization_id) == organization_id,
                    ),
                    or_(
                        col(ChannelBindingRow.conversation_id).is_(None),
                        col(ChannelBindingRow.conversation_id) == conversation_id,
                    ),
                )
                .values(
                    user_id=user_id,
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                )
                .returning(ChannelBindingRow)
            )
            binding = changed.scalar_one_or_none()
            if binding is None:
                raise ChannelOwnerConflict("channel binding cannot change owners or conversation")
            return _binding(binding)

    async def record_prepared(
        self, event_id: str, token: str, conversation_id: str, run_id: str, message_id: str
    ) -> InboxItem:
        for value in (conversation_id, run_id, message_id):
            bounded_identifier(value, limit=64)
        async with self._sessions() as session, session.begin():
            row = await _claimed(session, event_id, token)
            binding = await session.get(
                ChannelBindingRow, (row.connection_id, row.system_id, row.sender_id)
            )
            if binding is None or binding.conversation_id != conversation_id:
                raise ChannelClaimConflict("prepared conversation does not match sender binding")
            if row.state == InboxState.PREPARED:
                if (row.conversation_id, row.run_id, row.message_id) != (
                    conversation_id,
                    run_id,
                    message_id,
                ):
                    raise ChannelClaimConflict("prepared work cannot change turn identifiers")
                return _item(row)
            if row.state != InboxState.PREPARING:
                raise ChannelClaimConflict("work is not being prepared")
            changed = await session.exec(
                update(ChannelInboxRow)
                .where(
                    col(ChannelInboxRow.event_id) == event_id,
                    col(ChannelInboxRow.state) == InboxState.PREPARING.value,
                    col(ChannelInboxRow.claim_token) == token,
                )
                .values(
                    state=InboxState.PREPARED.value,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    message_id=message_id,
                )
                .returning(ChannelInboxRow)
            )
            prepared = changed.scalar_one_or_none()
            if prepared is None:
                # Another caller can commit this same preparation between the
                # read and CAS. Reuse its receipt without overwriting its IDs.
                await session.refresh(row)
                if row.state == InboxState.PREPARED and (
                    row.conversation_id,
                    row.run_id,
                    row.message_id,
                ) == (conversation_id, run_id, message_id):
                    return _item(row)
                raise ChannelClaimConflict("work changed during preparation")
            return _item(prepared)

    async def claim_execution(self, event_id: str, token: str) -> bool:
        async with self._sessions() as session, session.begin():
            changed = await session.exec(
                update(ChannelInboxRow)
                .where(
                    col(ChannelInboxRow.event_id) == event_id,
                    col(ChannelInboxRow.claim_token) == token,
                    col(ChannelInboxRow.state) == InboxState.PREPARED.value,
                )
                .values(state=InboxState.RUNNING.value)
                .returning(col(ChannelInboxRow.event_id))
            )
            return changed.scalar_one_or_none() is not None

    async def finish(self, event_id: str, token: str, state: InboxState) -> None:
        if state not in _TERMINAL | {InboxState.NEEDS_REVIEW}:
            raise ValueError("finish requires a terminal or review state")
        async with self._sessions() as session, session.begin():
            row = await _claimed(session, event_id, token, require_active=False)
            if row.state == state:
                return
            if row.state in _TERMINAL | {InboxState.NEEDS_REVIEW}:
                raise ChannelClaimConflict("finished or review work cannot be reopened")
            if state is InboxState.DENIED and row.state != InboxState.PREPARING:
                raise ChannelClaimConflict("prepared or running identity denial requires review")
            if row.state == InboxState.ANSWERED and state is not InboxState.DONE:
                raise ChannelClaimConflict("staged replies must be reconciled by delivery")
            changed = await session.exec(
                update(ChannelInboxRow)
                .where(
                    col(ChannelInboxRow.event_id) == event_id,
                    col(ChannelInboxRow.state) == row.state,
                )
                .values(state=state.value)
                .returning(col(ChannelInboxRow.event_id))
            )
            if changed.scalar_one_or_none() is None:
                raise ChannelClaimConflict("work changed before completion")
            if state is not InboxState.NEEDS_REVIEW:
                await _release(session, row)

    async def reset_conversation(self, event_id: str, token: str) -> int:
        async with self._sessions() as session, session.begin():
            row = await _claimed(session, event_id, token, require_active=False)
            if row.kind != "reset":
                raise ChannelClaimConflict("only a claimed reset event can reset a conversation")
            if row.state == InboxState.DONE and row.reset_generation is not None:
                return row.reset_generation
            if row.state != InboxState.PREPARING:
                raise ChannelClaimConflict("reset is not being prepared")
            binding = await session.get(
                ChannelBindingRow, (row.connection_id, row.system_id, row.sender_id)
            )
            if binding is None or binding.active_event_id != event_id:
                await session.refresh(row)
                if row.state == InboxState.DONE and row.reset_generation is not None:
                    return row.reset_generation
                raise ChannelClaimConflict("reset does not own its sender")
            generation = binding.generation + 1
            changed = await session.exec(
                update(ChannelInboxRow)
                .where(
                    col(ChannelInboxRow.event_id) == event_id,
                    col(ChannelInboxRow.state) == InboxState.PREPARING.value,
                    col(ChannelInboxRow.claim_token) == token,
                )
                .values(state=InboxState.DONE.value, reset_generation=generation)
                .returning(col(ChannelInboxRow.event_id))
            )
            if changed.scalar_one_or_none() is None:
                await session.refresh(row)
                if row.state == InboxState.DONE and row.reset_generation is not None:
                    return row.reset_generation
                raise ChannelClaimConflict("reset changed before its commit")
            reset_binding = await session.exec(
                update(ChannelBindingRow)
                .where(
                    _binding_condition(row),
                    col(ChannelBindingRow.active_event_id) == event_id,
                    col(ChannelBindingRow.generation) == binding.generation,
                )
                .values(conversation_id=None, generation=generation, active_event_id=None)
                .returning(col(ChannelBindingRow.connection_id))
            )
            if reset_binding.scalar_one_or_none() is None:
                raise ChannelClaimConflict("reset lost its sender generation")
            return generation

    async def stage_reply(
        self, event_id: str, token: str, response_id: str, parts: Sequence[str]
    ) -> tuple[StagedReply, ...]:
        return await stage_reply(self._sessions, event_id, token, response_id, parts)

    async def list_replies(self, event_id: str) -> tuple[StagedReply, ...]:
        return await list_replies(self._sessions, event_id)


def _binding_condition(row: ChannelInboxRow | type[ChannelInboxRow]) -> ColumnElement[bool]:
    return and_(
        col(ChannelBindingRow.connection_id) == row.connection_id,
        col(ChannelBindingRow.system_id) == row.system_id,
        col(ChannelBindingRow.sender_id) == row.sender_id,
    )


async def _inbound(session: AsyncSession, event_id: str) -> ChannelInboxRow | None:
    rows = await session.exec(
        select(ChannelInboxRow)
        .where(col(ChannelInboxRow.event_id) == event_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    return rows.first()


async def _claimed(
    session: AsyncSession, event_id: str, token: str, *, require_active: bool = True
) -> ChannelInboxRow:
    row = await _inbound(session, event_id)
    if row is None or not token or row.claim_token != token:
        raise ChannelClaimConflict("invalid channel work claim")
    if require_active:
        binding = await session.get(
            ChannelBindingRow, (row.connection_id, row.system_id, row.sender_id)
        )
        if binding is None or binding.active_event_id != event_id:
            raise ChannelClaimConflict("work no longer owns its sender")
    return row


async def _release(session: AsyncSession, row: ChannelInboxRow) -> None:
    await session.exec(
        update(ChannelBindingRow)
        .where(_binding_condition(row), col(ChannelBindingRow.active_event_id) == row.event_id)
        .values(active_event_id=None)
    )


def _binding(row: ChannelBindingRow) -> ChannelBinding:
    return ChannelBinding(
        ChannelAddress(row.connection_id, row.system_id, row.sender_id),
        row.generation,
        row.user_id,
        row.organization_id,
        row.conversation_id,
        row.active_event_id,
    )


def _item(row: ChannelInboxRow) -> InboxItem:
    assert row.sequence is not None
    received_at = ensure_utc(row.received_at)
    assert received_at is not None
    return InboxItem(
        row.event_id,
        row.sequence,
        InboundMessage(
            ChannelAddress(row.connection_id, row.system_id, row.sender_id),
            row.provider_message_id,
            row.content,
            received_at,
            ensure_utc(row.sent_at),
            cast(MessageKind, row.kind),
        ),
        InboxState(row.state),
        row.claim_token,
        row.conversation_id,
        row.run_id,
        row.message_id,
    )
