"""Contract tests for run repository implementations."""

from __future__ import annotations

import asyncio
import math

import pytest

from agent_engine.approvals.models import RunRecord, RunStatus
from agent_engine.runs.in_memory import (
    _EXPIRATION_CLEANUP_BATCH_SIZE,
    InMemoryRunRepository,
)
from agent_engine.runs.repository import RunRepository

pytestmark = pytest.mark.asyncio


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def read(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def run_repository() -> RunRepository:
    """Repository contract fixture; future adapters can reuse these tests."""
    return InMemoryRunRepository()


def _run(
    run_id: str,
    *,
    system_name: str = "system",
    status: RunStatus = RunStatus.RUNNING,
) -> RunRecord:
    return RunRecord(run_id=run_id, thread_id=run_id, system_name=system_name, status=status)


async def test_in_memory_run_repository_implements_contract() -> None:
    assert issubclass(InMemoryRunRepository, RunRepository)
    assert isinstance(InMemoryRunRepository(), RunRepository)


async def test_legacy_adapter_must_implement_atomic_leg_accounting() -> None:
    # Model an external adapter implementing every operation from the old
    # contract, but relying on the base class for keyed leg accounting.
    legacy_adapter = type(
        "LegacyRunRepository",
        (RunRepository,),
        {
            name: getattr(InMemoryRunRepository, name)
            for name in (
                "create_if_absent",
                "get",
                "get_many",
                "transition_if_allowed",
                "add_token_usage",
            )
        },
    )

    with pytest.raises(TypeError, match="add_leg_token_usage"):
        legacy_adapter()


async def test_base_leg_accounting_cannot_fall_back_to_unkeyed_addition(
    run_repository: RunRepository,
) -> None:
    await run_repository.create_if_absent(_run("r1"))

    with pytest.raises(NotImplementedError):
        await RunRepository.add_leg_token_usage(
            run_repository, "r1", leg_id="leg", input_tokens=4, output_tokens=1
        )

    stored = await run_repository.get("r1")
    assert stored is not None
    assert (stored.input_tokens, stored.output_tokens) == (None, None)


async def test_run_repository_registers_new_run(run_repository: RunRepository) -> None:
    record = _run("r1")

    created = await run_repository.create_if_absent(record)

    assert created is True
    assert await run_repository.get("r1") == record


async def test_run_repository_resolves_many_ids_at_once(run_repository: RunRepository) -> None:
    await run_repository.create_if_absent(_run("r1"))
    await run_repository.create_if_absent(_run("r2", status=RunStatus.COMPLETED))

    found = await run_repository.get_many(["r1", "r2", "unknown"])

    assert set(found) == {"r1", "r2"}
    assert found["r2"].status is RunStatus.COMPLETED
    assert await run_repository.get_many([]) == {}


async def test_run_repository_accumulates_reported_token_usage(
    run_repository: RunRepository,
) -> None:
    record = _run("r1")
    await run_repository.create_if_absent(record)

    first = await run_repository.add_token_usage("r1", input_tokens=10, output_tokens=None)
    second = await run_repository.add_token_usage("r1", input_tokens=5, output_tokens=3)

    assert first is record
    assert second is record
    assert record.input_tokens == 15
    assert record.output_tokens == 3


async def test_run_repository_token_usage_does_not_create_an_unknown_run(
    run_repository: RunRepository,
) -> None:
    assert await run_repository.add_token_usage("missing", input_tokens=1, output_tokens=1) is None


async def test_run_repository_does_not_overwrite_existing_run(
    run_repository: RunRepository,
) -> None:
    original = _run("r1", system_name="original")
    await run_repository.create_if_absent(original)
    assert await run_repository.transition_if_allowed("r1", RunStatus.COMPLETED) is True

    created = await run_repository.create_if_absent(_run("r1", system_name="replacement"))

    assert created is False
    stored = await run_repository.get("r1")
    assert stored is not None
    assert stored == original
    assert stored.system_name == "original"
    assert stored.status == RunStatus.COMPLETED


async def test_run_repository_registers_different_ids(
    run_repository: RunRepository,
) -> None:
    first_created = await run_repository.create_if_absent(_run("r1"))
    second_created = await run_repository.create_if_absent(_run("r2"))

    assert first_created is True
    assert second_created is True
    assert await run_repository.get("r1") is not None
    assert await run_repository.get("r2") is not None


async def test_many_concurrent_run_registrations_have_one_winner() -> None:
    repository = InMemoryRunRepository()
    results = await asyncio.gather(
        *(
            repository.create_if_absent(_run("shared", system_name=f"system-{index}"))
            for index in range(100)
        )
    )

    assert sum(results) == 1
    assert await repository.get("shared") is not None


async def test_concurrent_creates_for_unrelated_runs_all_succeed() -> None:
    repository = InMemoryRunRepository()
    run_ids = [f"run-{index}" for index in range(100)]

    results = await asyncio.gather(
        *(repository.create_if_absent(_run(run_id)) for run_id in run_ids)
    )
    stored = await asyncio.gather(*(repository.get(run_id) for run_id in run_ids))

    assert all(results)
    assert all(record is not None for record in stored)


async def test_transition_and_duplicate_create_for_same_run_are_safe() -> None:
    repository = InMemoryRunRepository()
    original = _run("shared", system_name="original")
    await repository.create_if_absent(original)
    duplicate_result, transition_result = await asyncio.gather(
        repository.create_if_absent(_run("shared", system_name="replacement")),
        repository.transition_if_allowed("shared", RunStatus.COMPLETED),
    )

    assert duplicate_result is False
    assert transition_result is True
    assert await repository.get("shared") == original
    assert original.status == RunStatus.COMPLETED


async def test_transition_if_allowed_is_a_single_process_local_operation() -> None:
    repository = InMemoryRunRepository()
    await repository.create_if_absent(_run("shared"))

    completed, cancelled = await asyncio.gather(
        repository.transition_if_allowed("shared", RunStatus.COMPLETED),
        repository.transition_if_allowed("shared", RunStatus.CANCELLED),
    )

    assert sum((completed, cancelled)) == 1
    stored = await repository.get("shared")
    assert stored is not None
    assert stored.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}


async def test_transition_if_allowed_leaves_missing_or_terminal_runs_unchanged() -> None:
    repository = InMemoryRunRepository()

    assert await repository.transition_if_allowed("missing", RunStatus.COMPLETED) is False

    await repository.create_if_absent(_run("finished"))
    assert await repository.transition_if_allowed("finished", RunStatus.COMPLETED) is True
    assert await repository.transition_if_allowed("finished", RunStatus.CANCELLED) is False
    stored = await repository.get("finished")
    assert stored is not None
    assert stored.status == RunStatus.COMPLETED


@pytest.mark.parametrize(
    "terminal_status",
    [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED],
)
async def test_terminal_runs_expire_after_configured_ttl(terminal_status: RunStatus) -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=60, clock=clock.read)
    record = _run("finished")
    await repository.create_if_absent(record)
    assert await repository.transition_if_allowed("finished", terminal_status) is True

    clock.advance(59)
    assert await repository.get("finished") == record

    clock.advance(1)
    assert await repository.get("finished") is None


@pytest.mark.parametrize(
    "active_status",
    [RunStatus.RUNNING, RunStatus.PENDING_APPROVAL, RunStatus.RESUMING],
)
async def test_active_runs_do_not_expire(active_status: RunStatus) -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=1, clock=clock.read)
    record = RunRecord(
        run_id="active",
        thread_id="active",
        system_name="system",
        status=active_status,
    )
    await repository.create_if_absent(record)

    clock.advance(10)

    assert await repository.get("active") == record


async def test_expired_run_id_can_be_registered_again() -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=1, clock=clock.read)
    original = _run("reused", system_name="original")
    await repository.create_if_absent(original)
    await repository.transition_if_allowed("reused", RunStatus.COMPLETED)
    clock.advance(1)
    assert await repository.get("reused") is None

    replacement = _run("reused", system_name="replacement")

    assert await repository.create_if_absent(replacement) is True
    assert await repository.get("reused") == replacement


async def test_existing_run_registration_preserves_original_expiration() -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=10, clock=clock.read)
    original = _run("finished", system_name="original")
    await repository.create_if_absent(original)
    await repository.transition_if_allowed("finished", RunStatus.COMPLETED)
    clock.advance(5)
    original_entry = repository._runs["finished"]
    original_expiration = original_entry.expires_at
    original_queue = tuple(repository._expiration_queue)

    created = await repository.create_if_absent(_run("finished", system_name="replacement"))

    assert created is False
    assert repository._runs["finished"] is original_entry
    assert original_entry.expires_at == original_expiration
    assert tuple(repository._expiration_queue) == original_queue
    clock.advance(4)
    assert await repository.get("finished") == original
    clock.advance(1)
    assert await repository.get("finished") is None


async def test_create_if_absent_does_not_evict_expired_records() -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=1, clock=clock.read)
    expired = _run("expired")
    await repository.create_if_absent(expired)
    await repository.transition_if_allowed("expired", RunStatus.COMPLETED)
    clock.advance(1)

    assert await repository.create_if_absent(_run("expired")) is False
    assert await repository.create_if_absent(_run("new")) is True
    assert repository._runs["expired"].record is expired

    assert await repository.get("expired") is None


async def test_stale_expiration_cannot_delete_newer_record() -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=1, clock=clock.read)
    original = _run("reused", status=RunStatus.COMPLETED)
    await repository.create_if_absent(original)
    clock.advance(1)
    assert await repository.get("reused") is None

    replacement = _run("reused")
    assert await repository.create_if_absent(replacement) is True
    assert await repository.transition_if_allowed("reused", RunStatus.COMPLETED) is True

    assert await repository.get("reused") is replacement


async def test_expiration_cleanup_work_is_bounded_per_transition() -> None:
    clock = ManualClock()
    repository = InMemoryRunRepository(terminal_ttl_seconds=1, clock=clock.read)
    expired_count = 100
    for index in range(expired_count):
        await repository.create_if_absent(_run(f"expired-{index}", status=RunStatus.COMPLETED))
    clock.advance(1)

    assert await repository.transition_if_allowed("missing", RunStatus.COMPLETED) is False

    remaining_expired = sum(run_id.startswith("expired-") for run_id in repository._runs)
    removed = expired_count - remaining_expired
    assert 0 < removed <= _EXPIRATION_CLEANUP_BATCH_SIZE
    assert len(repository._expiration_queue) > 0


@pytest.mark.parametrize("ttl", [0, -1, math.inf, math.nan])
async def test_terminal_run_ttl_must_be_positive_and_finite(ttl: float) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        InMemoryRunRepository(terminal_ttl_seconds=ttl)
