"""SQL adapter for authoritative engine run lifecycle state."""

from __future__ import annotations

import time
from collections.abc import Collection
from typing import Any

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_engine.approvals.models import RunRecord, RunStatus, can_transition_run
from agent_engine.runs.repository import RunRepository
from agent_manager.infrastructure.persistence.tables import RunRecordRow


class SqlRunRepository(RunRepository):
    """Durable, atomically transitioned run records for manager deployments."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_if_absent(self, record: RunRecord) -> bool:
        async with self._sessions() as session, session.begin():
            if await session.get(RunRecordRow, record.run_id) is not None:
                return False
            try:
                async with session.begin_nested():
                    session.add(_row(record))
            except IntegrityError:
                return False
        return True

    async def get(self, run_id: str) -> RunRecord | None:
        async with self._sessions() as session:
            row = await session.get(RunRecordRow, run_id)
        return _record(row) if row is not None else None

    async def get_many(self, run_ids: Collection[str]) -> dict[str, RunRecord]:
        """Resolve every requested id in one statement."""
        wanted = set(run_ids)
        if not wanted:
            return {}
        async with self._sessions() as session:
            result = await session.exec(
                select(RunRecordRow).where(col(RunRecordRow.run_id).in_(wanted))
            )
            rows = result.all()
        return {row.run_id: _record(row) for row in rows}

    async def transition_if_allowed(self, run_id: str, target: RunStatus) -> bool:
        sources = frozenset(status for status in RunStatus if can_transition_run(status, target))
        if not sources:
            return False
        async with self._sessions() as session, session.begin():
            changed = await session.exec(
                update(RunRecordRow)
                .where(
                    col(RunRecordRow.run_id) == run_id,
                    col(RunRecordRow.status).in_([status.value for status in sources]),
                )
                .values(status=target.value, updated_at=time.time())
            )
        return int(_rowcount(changed)) == 1

    async def add_token_usage(
        self,
        run_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        values: dict[str, object] = {"updated_at": time.time()}
        if input_tokens is not None:
            values["input_tokens"] = func.coalesce(RunRecordRow.input_tokens, 0) + input_tokens
        if output_tokens is not None:
            values["output_tokens"] = func.coalesce(RunRecordRow.output_tokens, 0) + output_tokens
        async with self._sessions() as session, session.begin():
            # Return the charged row in the write transaction. A separate read
            # after commit could fail and cause the caller to add the leg twice.
            result = await session.exec(
                update(RunRecordRow)
                .where(col(RunRecordRow.run_id) == run_id)
                .values(**values)
                .returning(RunRecordRow)
            )
            row = result.scalar_one_or_none()
            record = _record(row) if row is not None else None
        return record


def _rowcount(result: Any) -> int:
    return int(result.rowcount)


def _row(record: RunRecord) -> RunRecordRow:
    return RunRecordRow(
        run_id=record.run_id,
        thread_id=record.thread_id,
        system_name=record.system_name,
        status=record.status.value,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _record(row: RunRecordRow) -> RunRecord:
    return RunRecord(
        run_id=row.run_id,
        thread_id=row.thread_id,
        system_name=row.system_name,
        status=RunStatus(row.status),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
