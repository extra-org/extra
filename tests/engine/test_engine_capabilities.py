"""Pin the optional capabilities the manager detects on LangGraphEngine.

The capabilities are structural Protocols, so nothing forces LangGraphEngine to
keep matching them. The annotated bindings are the signature-level check (mypy
rejects a drifted signature); the isinstance assertions cover the runtime
detection ConversationService actually performs, which only sees attribute names.
"""

from __future__ import annotations

from pathlib import Path

from agent_engine.engine.approval_cancellation_engine import ApprovalCancellationEngine
from agent_engine.engine.approval_engine import ApprovalEngine
from agent_engine.engine.approval_streaming_engine import ApprovalStreamingEngine
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.run_status_engine import RunStatusEngine
from agent_engine.engine.text_completion_engine import TextCompletionEngine


def test_langgraph_engine_provides_the_approval_capability(tmp_path: Path) -> None:
    engine: ApprovalEngine = LangGraphEngine(tmp_path)
    assert isinstance(engine, ApprovalEngine)


def test_langgraph_engine_provides_the_approval_streaming_capability(tmp_path: Path) -> None:
    engine: ApprovalStreamingEngine = LangGraphEngine(tmp_path)
    assert isinstance(engine, ApprovalStreamingEngine)


def test_langgraph_engine_provides_the_approval_cancellation_capability(tmp_path: Path) -> None:
    engine: ApprovalCancellationEngine = LangGraphEngine(tmp_path)
    assert isinstance(engine, ApprovalCancellationEngine)


def test_langgraph_engine_provides_the_run_status_capability(tmp_path: Path) -> None:
    engine: RunStatusEngine = LangGraphEngine(tmp_path)
    assert isinstance(engine, RunStatusEngine)


def test_langgraph_engine_provides_the_text_completion_capability(tmp_path: Path) -> None:
    engine: TextCompletionEngine = LangGraphEngine(tmp_path)
    assert isinstance(engine, TextCompletionEngine)
