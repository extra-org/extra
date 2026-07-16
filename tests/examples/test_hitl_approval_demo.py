"""Runnable HITL example scenarios through the real production engine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "examples" / "hitl-approval-demo" / "run_demo.py"


def _run(scenario: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), scenario],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


@pytest.mark.parametrize(
    ("scenario", "approval_requests", "tool_executions"),
    [
        ("allow-once", 2, 2),
        ("allow-session", 1, 2),
        ("new-session", 2, 2),
        ("different-agent", 2, 2),
        ("deny", 2, 0),
        ("auto", 0, 1),
    ],
)
def test_demo_scenario(scenario: str, approval_requests: int, tool_executions: int) -> None:
    output = _run(scenario)

    assert f"approval_requests: {approval_requests}" in output
    assert f"tool_executions: {tool_executions}" in output


def test_allow_for_session_proves_saved_permission_and_skipped_prompt() -> None:
    output = _run("allow-session")

    assert "Session approval saved:" in output
    assert "session_permission_found: True" in output
    assert output.count("approval_required: true") == 1
    assert output.count("approval_prompt_skipped: true") == 1


def test_deny_never_reaches_tool_execution_boundary() -> None:
    output = _run("deny")

    assert "[TOOL EXECUTED]" not in output
    assert output.count("approval_required: true") == 2


def test_auto_never_requests_approval() -> None:
    output = _run("auto")

    assert "approval_required: true" not in output
    assert "[TOOL EXECUTED] write_demo_message" in output
