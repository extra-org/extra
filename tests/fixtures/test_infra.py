from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from tests.fixtures.utils import FakeEngine, load_test_system


@pytest.fixture(autouse=True)
def _cleanup_shared() -> Iterator[None]:
    yield
    sys.modules.pop("shared", None)


def test_spec_validates_offline() -> None:
    load_test_system()


def test_fixture_execution_policy_parsed_from_yaml() -> None:
    spec, _ = load_test_system()
    assert spec.execution.max_iterations == 10
    assert spec.execution.max_tool_calls == 5
    assert spec.execution.max_tool_calls_per_agent == 3
    assert spec.execution.max_child_agent_calls == 4
    assert spec.execution.allow_duplicate_tool_calls is False


@pytest.mark.asyncio
async def test_engine_builds_from_fixture() -> None:
    async with FakeEngine():
        pass


@pytest.mark.asyncio
async def test_orchestrator_routes_to_greeting() -> None:
    async with FakeEngine() as engine:
        result = await engine.run("greeting_agent hello")
        assert "root_router/greeting_agent" in result.visited
        assert result.answer


@pytest.mark.asyncio
async def test_orchestrator_routes_to_echo_and_calls_tool() -> None:
    async with FakeEngine() as engine:
        result = await engine.run("echo_agent echo test")
        assert "root_router/echo_agent" in result.visited
        echo_record = next(record for record in result.used_tools if record.name == "echo_tool")
        assert echo_record.status == "succeeded"
        assert echo_record.error is None
        assert result.answer


@pytest.mark.asyncio
async def test_resolvers_resolve_at_runtime() -> None:
    async with FakeEngine() as engine:
        result = await engine.run("hello")
        assert result.status == "completed"
