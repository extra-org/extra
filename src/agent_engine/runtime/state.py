"""State passed between LangGraph nodes for one request.

This is the per-request data that flows through the graph. Each node returns a
partial update; LangGraph merges it into the running state.
"""

from __future__ import annotations

from typing import Any, TypedDict

from agent_engine.runtime.tool_models import ToolUsageRecord


class GraphState(TypedDict, total=False):
    """Mutable state carried through one run of the compiled LangGraph.

    Kept fully serializable so it can be checkpointed: request-scoped callbacks
    (answer/route/token streaming) live on the ``current_streams`` contextvar,
    not here (see ``agent_engine.runtime.streaming``).
    """

    message: str
    """The user's incoming message."""

    history: list[dict[str, str]]
    """Prior user/assistant turns, oldest-first, serialized for checkpointing."""

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
