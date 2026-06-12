"""Runtime engine — compile once, run many times.

Usage::

    engine = Engine(loaded)          # compile + build graph (once)
    result = engine.run("message")   # invoke (per request)
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from dataclasses import dataclass

from agentplatform.compiler import compile_spec
from agentplatform.runtime.langgraph_builder import build_langgraph
from agentplatform.runtime.mcp_manager import MCPClientFactory, MCPManager
from agentplatform.runtime.tool_registry import MCPToolProvider, ToolRegistry, LocalToolProvider
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

    def __init__(
        self,
        loaded: LoadedSpec,
        *,
        mcp_client_factory: MCPClientFactory | None = None,
    ) -> None:
        graph = compile_spec(loaded.spec)
        self._system_name = graph.system_name
        self._mcp_manager = MCPManager(
            loaded.spec.mcps,
            client_factory=mcp_client_factory,
        )

        self._tool_registry = ToolRegistry(
            providers=[
                LocalToolProvider(spec=loaded.spec),
                MCPToolProvider(
                    spec=loaded.spec,
                    mcp_manager=self._mcp_manager,
                ),
            ]
        )

        self._app = build_langgraph(
            graph,
            agents_yml=loaded.source_path,
            tool_registry=self._tool_registry,
        )

    @property
    def system_name(self) -> str:
        return self._system_name

    @property
    def mcp_manager(self) -> MCPManager:
        """Return the engine-owned MCP manager.

        The manager is created once with the engine, but it is not started
        automatically by ``run``.
        """
        return self._mcp_manager

    @property
    def tool_registry(self) -> ToolRegistry:
        """Return the engine-owned tool registry."""
        return self._tool_registry

    def start(self) -> None:
        """Start long-lived runtime infrastructure.

        Currently, this starts MCP clients. This is intentionally separate from
        ``run`` so the CLI can keep running without a real MCP client.
        """
        asyncio.run(self._mcp_manager.start())

    def stop(self) -> None:
        """Stop long-lived runtime infrastructure."""
        asyncio.run(self._mcp_manager.stop())

    def run(self, message: str) -> RunResult:
        """Invoke the agent system with *message* and return the result."""
        input_state: dict[str, object] = {"message": message}
        result = self._app.invoke(cast(Any, input_state))

        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer=result.get("answer", ""),
        )
