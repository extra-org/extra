"""State passed between LangGraph nodes for one request.

This is the per-request data that flows through the graph. Each node returns a
partial update; LangGraph merges it into the running state.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from agent_engine.runtime.tool_models import ToolUsageRecord


class GraphState(TypedDict, total=False):
    """Mutable state carried through one run of the compiled LangGraph."""

    message: str
    """The user's incoming message."""

    visited: list[str]
    """Ordered node paths visited — the execution trace."""

    answer: str
    """The final answer produced by the agent (leaf) node that handled it."""

    used_tools: list[ToolUsageRecord]
    """Runtime-observed tool calls, in call order."""

    run_context: dict[str, Any]
    """Generic request/run context supplied by the host application, CLI, or API.

    The engine does not define a universal authorization schema. Hosts may pass
    user/session/auth/metadata values here, and runtime components such as
    protected-node access filtering may consult them.
    """

    answer_stream: Callable[[str], None]
    """Optional callback for final assistant answer chunks."""

    route_stream: Callable[[tuple[str, ...]], None]
    """Optional callback for the final route once the selected agent is known."""

    token_stream: Callable[[int, int], None]
    """Optional callback invoked after every LLM call with (input_tokens, output_tokens)."""
