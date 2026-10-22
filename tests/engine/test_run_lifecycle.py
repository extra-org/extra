"""Run lifecycle tests at the run-persistence contract boundary."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import pytest

from agent_engine.approvals.approval_manager import ApprovalManager
from agent_engine.approvals.in_memory_approval_repository import InMemoryApprovalRepository
from agent_engine.approvals.models import RunRecord, RunStatus
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.langgraph.execution.run_lifecycle import RunLifecycle
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runs.repository import RunRepository
from agent_engine.runtime.hooks import HookManager, RunContext


class RegistrationSpy(RunRepository):
    """Accept registration but reject a lifecycle-owned preflight read."""

    def __init__(self) -> None:
        self.registered: list[RunRecord] = []
        self.transitions: list[tuple[str, RunStatus]] = []

    async def create_if_absent(self, record: RunRecord) -> bool:
        self.registered.append(record)
        return True

    async def get(self, run_id: str) -> RunRecord | None:
        raise AssertionError("RunLifecycle must not read before registration")

    async def get_many(self, run_ids: Collection[str]) -> dict[str, RunRecord]:
        raise AssertionError("RunLifecycle must not read before registration")

    async def transition_if_allowed(self, run_id: str, target: RunStatus) -> bool:
        self.transitions.append((run_id, target))
        return True

    async def add_token_usage(
        self,
        run_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        del run_id, input_tokens, output_tokens
        raise AssertionError("RunLifecycle must not record engine-owned usage")

    async def add_leg_token_usage(
        self,
        run_id: str,
        *,
        leg_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord | None:
        del run_id, leg_id, input_tokens, output_tokens
        raise AssertionError("RunLifecycle must not record engine-owned usage")


class RunIdReplacingHookManager(HookManager):
    async def run_run_start(self, context: RunContext) -> RunContext:
        return context.replace(run_id="replaced-run")


async def test_begin_delegates_registration_as_one_repository_operation() -> None:
    repository = RegistrationSpy()
    lifecycle = RunLifecycle(
        system_name="system",
        hook_manager=HookManager.empty(),
        run_repository=repository,
    )

    context = await lifecycle.begin(RunContext(run_id="run-1"))

    assert context.run_id == "run-1"
    assert [(record.run_id, record.status) for record in repository.registered] == [
        ("run-1", RunStatus.RUNNING)
    ]

    await lifecycle.cancel(context)

    assert repository.transitions == [("run-1", RunStatus.CANCELLED)]


async def test_begin_rejects_hook_replacement_of_authoritative_run_id() -> None:
    repository = RegistrationSpy()
    lifecycle = RunLifecycle(
        system_name="system",
        hook_manager=RunIdReplacingHookManager(),
        run_repository=repository,
    )

    with pytest.raises(ValueError, match=r"cannot replace.*run_id"):
        await lifecycle.begin(RunContext(run_id="run-1"))

    assert repository.registered == []


async def test_begin_does_not_reset_an_existing_run() -> None:
    repository = InMemoryRunRepository()
    existing = RunRecord(
        run_id="run-1",
        thread_id="run-1",
        system_name="original",
        status=RunStatus.PENDING_APPROVAL,
    )
    await repository.create_if_absent(existing)
    lifecycle = RunLifecycle(
        system_name="replacement",
        hook_manager=HookManager.empty(),
        run_repository=repository,
    )

    await lifecycle.begin(RunContext(run_id="run-1"))

    stored = await repository.get("run-1")
    assert stored == existing
    assert stored.system_name == "original"
    assert stored.status == RunStatus.PENDING_APPROVAL


def test_engine_rejects_ambiguous_run_repository_composition(tmp_path: Path) -> None:
    manager_runs = InMemoryRunRepository()
    manager = ApprovalManager(
        run_repository=manager_runs,
        approval_repository=InMemoryApprovalRepository(),
    )

    with pytest.raises(ValueError, match="approval_manager or run_repository"):
        LangGraphEngine(
            tmp_path,
            approval_manager=manager,
            run_repository=InMemoryRunRepository(),
        )
