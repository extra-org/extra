"""The engine maps RunContext identity onto Langfuse's trace-grouping metadata."""

from __future__ import annotations

from agent_engine.engine.langgraph.engine import _trace_metadata
from agent_engine.runtime.hooks import RunContext


def test_conversation_id_becomes_langfuse_session() -> None:
    assert _trace_metadata(RunContext(conversation_id="sess-1")) == {
        "langfuse_session_id": "sess-1"
    }


def test_user_id_becomes_langfuse_user() -> None:
    md = _trace_metadata(RunContext(conversation_id="s", user_id="u1"))
    assert md == {"langfuse_session_id": "s", "langfuse_user_id": "u1"}


def test_empty_context_yields_no_metadata() -> None:
    # Nothing to group on → empty dict (no-op for every callback).
    assert _trace_metadata(RunContext()) == {}
