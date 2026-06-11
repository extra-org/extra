"""Runtime engine — compile once, run many times.

Usage::

    engine = Engine(loaded)          # compile + build graph (once)
    result = engine.run("message")   # invoke (per request)
"""

from __future__ import annotations

from dataclasses import dataclass

from agentplatform.compiler import compile_spec
from agentplatform.runtime.langgraph_builder import build_langgraph
from agentplatform.spec.loader import LoadedSpec


@dataclass(frozen=True)
class RunResult:
    """The outcome of one agent run."""

    system_name: str
    visited: list[str]
    answer: str


class Engine:
    """Compiled, ready-to-run agent system.

    Construction is the expensive step: spec → compile → build graph.
    Call :meth:`run` once per message — no recompilation on each request.

    This is the single entry point for all callers: CLI, API server,
    batch runner, test harness — all create one ``Engine`` and reuse it.
    """

    def __init__(self, loaded: LoadedSpec) -> None:
        graph = compile_spec(loaded.spec)
        self._system_name = graph.system_name
        self._app = build_langgraph(graph, base_dir=loaded.source_path.parent)

    @property
    def system_name(self) -> str:
        return self._system_name

    def run(self, message: str) -> RunResult:
        """Invoke the agent system with *message* and return the result."""
        result = self._app.invoke({"message": message})
        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer=result.get("answer", ""),
        )
