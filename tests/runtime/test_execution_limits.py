"""Unit tests for the ExecutionLimiter + ExecutionPolicy config parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_engine.core.execution import ExecutionPolicy
from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.execution import ExecutionLimiter, ExecutionLimitExceeded

_BASE = "system: {name: t}\nagents: {a: {description: d}}\ngraph: {a: }\n"


def _parse(tmp_path: Path, extra: str = ""):
    cfg = tmp_path / "agents.yml"
    cfg.write_text(_BASE + extra, encoding="utf-8")
    return YAMLParser().parse(str(cfg))


# -- config parsing ----------------------------------------------------------


def test_defaults_applied_when_execution_omitted(tmp_path: Path) -> None:
    spec = _parse(tmp_path)
    assert spec.execution == ExecutionPolicy(
        max_iterations=20,
        max_tool_calls=10,
        max_tool_calls_per_agent=4,
        max_child_agent_calls=8,
        allow_duplicate_tool_calls=False,
    )


def test_custom_execution_policy_parsed(tmp_path: Path) -> None:
    spec = _parse(
        tmp_path,
        "execution:\n"
        "  max_iterations: 5\n"
        "  max_tool_calls: 3\n"
        "  max_tool_calls_per_agent: 2\n"
        "  max_child_agent_calls: 1\n"
        "  allow_duplicate_tool_calls: true\n",
    )
    assert spec.execution == ExecutionPolicy(5, 3, 2, 1, True)


def test_partial_execution_block_fills_missing_with_defaults(tmp_path: Path) -> None:
    spec = _parse(tmp_path, "execution: {max_tool_calls: 2}\n")
    assert spec.execution.max_tool_calls == 2
    assert spec.execution.max_iterations == 20  # default preserved


@pytest.mark.parametrize(
    "bad",
    [
        "execution: {max_iterations: 0}\n",
        "execution: {max_tool_calls: -1}\n",
        "execution: {max_tool_calls_per_agent: 0}\n",
        "execution: {max_child_agent_calls: -3}\n",
        "execution: {max_iterations: true}\n",  # bool not accepted for int field
        "execution: {max_iterations: 1.5}\n",  # float not accepted
        "execution: {allow_duplicate_tool_calls: 5}\n",  # non-bool
        "execution: [1, 2]\n",  # not a mapping
    ],
)
def test_invalid_execution_config_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ParseError):
        _parse(tmp_path, bad)


# -- limiter semantics -------------------------------------------------------


def test_iterations_capped_per_node() -> None:
    lim = ExecutionLimiter(ExecutionPolicy(max_iterations=2))
    lim.register_iteration("n")
    lim.register_iteration("n")
    with pytest.raises(ExecutionLimitExceeded) as e:
        lim.register_iteration("n")
    assert e.value.limit_name == "max_iterations"
    # a different node has its own budget
    lim.register_iteration("other")


def test_total_tool_calls_capped() -> None:
    lim = ExecutionLimiter(ExecutionPolicy(max_tool_calls=2, max_tool_calls_per_agent=99))
    lim.register_tool_call("a", "t", {"i": 1})
    lim.register_tool_call("a", "t", {"i": 2})
    with pytest.raises(ExecutionLimitExceeded) as e:
        lim.register_tool_call("a", "t", {"i": 3})
    assert e.value.limit_name == "max_tool_calls"


def test_per_agent_tool_calls_capped() -> None:
    lim = ExecutionLimiter(ExecutionPolicy(max_tool_calls=99, max_tool_calls_per_agent=1))
    lim.register_tool_call("a", "t", {"i": 1})
    with pytest.raises(ExecutionLimitExceeded) as e:
        lim.register_tool_call("a", "t", {"i": 2})
    assert e.value.limit_name == "max_tool_calls_per_agent"
    # a different agent still has budget
    lim.register_tool_call("b", "t", {"i": 1})


def test_duplicate_tool_call_blocked_and_not_counted() -> None:
    lim = ExecutionLimiter(ExecutionPolicy(allow_duplicate_tool_calls=False, max_tool_calls=99))
    lim.register_tool_call("a", "t", {"x": 1})
    with pytest.raises(ExecutionLimitExceeded) as e:
        lim.register_tool_call("a", "t", {"x": 1})  # identical
    assert e.value.limit_name == "duplicate_tool_call"
    assert lim.state.total_tool_calls == 1  # blocked call was not counted


def test_duplicates_allowed_when_policy_permits() -> None:
    lim = ExecutionLimiter(ExecutionPolicy(allow_duplicate_tool_calls=True, max_tool_calls=99))
    lim.register_tool_call("a", "t", {"x": 1})
    lim.register_tool_call("a", "t", {"x": 1})  # no raise
    assert lim.state.total_tool_calls == 2


def test_child_agent_calls_capped() -> None:
    lim = ExecutionLimiter(ExecutionPolicy(max_child_agent_calls=1))
    lim.register_child_call("root", "child")
    with pytest.raises(ExecutionLimitExceeded) as e:
        lim.register_child_call("root", "child")
    assert e.value.limit_name == "max_child_agent_calls"
