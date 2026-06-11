"""State passed between LangGraph nodes for one request.

This is the per-request data that flows through the graph. Each node returns a
partial update; LangGraph merges it into the running state.
"""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict, total=False):
    """Mutable state carried through one run of the compiled LangGraph."""

    message: str
    """The user's incoming message."""

    route_hint: str
    """Optional node id/instance id an orchestrator should prefer when routing.

    Placeholder until real LLM-based routing lands; lets us drive and test the
    topology deterministically.
    """

    visited: list[str]
    """Ordered instance ids visited — the execution trace."""

    answer: str
    """The final answer produced by the agent (leaf) node that handled it."""
