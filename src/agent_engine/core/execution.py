"""Execution policy — conservative, runtime-enforced limits for one run.

A pure domain value object (no runtime imports). Parsed from the optional
top-level ``execution:`` YAML block; when absent, the conservative defaults below
apply. Enforcement lives in ``agent_engine.runtime.execution``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPolicy:
    """Per-run guardrails against runaway loops and repeated calls.

    * ``max_iterations`` — model→tools→model rounds allowed for a single node.
    * ``max_tool_calls`` — total local/MCP tool calls per run.
    * ``max_tool_calls_per_agent`` — tool calls per agent per run.
    * ``max_child_agent_calls`` — child-agent invocations (orchestrator→child) per run.
    * ``allow_duplicate_tool_calls`` — if False, a repeat of an identical call
      (same agent + tool + serialized args) is blocked.
    """

    max_iterations: int = 20
    max_tool_calls: int = 10
    max_tool_calls_per_agent: int = 4
    max_child_agent_calls: int = 8
    allow_duplicate_tool_calls: bool = False


#: The conservative defaults applied when ``execution:`` is omitted from the YAML.
DEFAULT_EXECUTION_POLICY = ExecutionPolicy()

#: Integer policy fields that must be positive.
EXECUTION_INT_FIELDS = (
    "max_iterations",
    "max_tool_calls",
    "max_tool_calls_per_agent",
    "max_child_agent_calls",
)
