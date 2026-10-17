"""One SQL transaction accepts messages, delivery events, and safe quarantine."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_manager.domain.channels.models import ChannelMessageConflict, stable_id
from agent_manager.domain.channels.webhooks import (
    IssueReason,
    WebhookBatch,
    WebhookIssue,
    WebhookReceipt,
    WebhookRepository,
)
from agent_manager.domain.pagination import ensure_utc
from agent_manager.infrastructure.persistence.channels.intake import ingest_in_session, sql_insert
from agent_manager.infrastructure.persistence.channels.tables import (
    ChannelDeliveryEventRow,
    ChannelWebhookReceiptRow,
)


class SqlWebhookRepository(WebhookRepository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def accept(
        self,
        connection_id: str,
        system_id: str,
        body_digest: str,
        received_at: datetime,
        batch: WebhookBatch,
    ) -> WebhookReceipt:
        if any(
            message.address.connection_id != connection_id or message.address.system_id != system_id
            for message in batch.messages
        ):
            raise ValueError("webhook messages must match the configured target")
        receipt_id = stable_id("webhook", connection_id, body_digest)
        async with self._sessions() as session, session.begin():
            inserted = await session.exec(
                sql_insert(session, ChannelWebhookReceiptRow)
                .values(
                    receipt_id=receipt_id,
                    connection_id=connection_id,
                    system_id=system_id,
                    body_digest=body_digest,
                    received_at=received_at,
                    event_ids=[],
                    delivery_event_ids=[],
                    issues=[],
                )
                .on_conflict_do_nothing(index_elements=["connection_id", "body_digest"])
                .returning(col(ChannelWebhookReceiptRow.receipt_id))
            )
            created = inserted.scalar_one_or_none() is not None
            row = await session.get(ChannelWebhookReceiptRow, receipt_id)
            assert row is not None
            if not created:
                return _receipt(row)
            event_ids: list[str] = []
            issues = list(batch.issues)
            for index, message in enumerate(batch.messages):
                try:
                    inbox = await ingest_in_session(session, message)
                    event_ids.append(inbox.event_id)
                except ChannelMessageConflict:
                    issues.append(WebhookIssue(f"messages.{index}", "conflicting_message"))
            delivery_ids: list[str] = []
            for event in batch.delivery_events:
                event_id = stable_id(
                    "delivery",
                    connection_id,
                    event.provider_message_id,
                    event.recipient_id,
                    event.status,
                    event.observed_at.isoformat(),
                )
                await session.exec(
                    sql_insert(session, ChannelDeliveryEventRow)
                    .values(
                        event_id=event_id,
                        connection_id=connection_id,
                        system_id=system_id,
                        provider_message_id=event.provider_message_id,
                        recipient_id=event.recipient_id,
                        status=event.status,
                        observed_at=event.observed_at,
                        received_at=received_at,
                    )
                    .on_conflict_do_nothing(index_elements=["event_id"])
                )
                delivery_ids.append(event_id)
            if len(issues) > 256:
                issues = [*issues[:255], WebhookIssue("body", "issue_limit")]
            row.event_ids = list(dict.fromkeys(event_ids))
            row.delivery_event_ids = list(dict.fromkeys(delivery_ids))
            row.issues = [{"location": issue.location, "reason": issue.reason} for issue in issues]
            session.add(row)
            await session.flush()
            return _receipt(row)

    async def get_receipt(self, receipt_id: str) -> WebhookReceipt | None:
        async with self._sessions() as session:
            row = await session.get(ChannelWebhookReceiptRow, receipt_id)
            return _receipt(row) if row is not None else None


def _receipt(row: ChannelWebhookReceiptRow) -> WebhookReceipt:
    return WebhookReceipt(
        row.receipt_id,
        row.connection_id,
        row.system_id,
        row.body_digest,
        ensure_utc(row.received_at) or row.received_at,
        tuple(row.event_ids),
        tuple(row.delivery_event_ids),
        tuple(
            WebhookIssue(issue["location"], cast(IssueReason, issue["reason"]))
            for issue in row.issues
        ),
    )
