"""Runtime engine — compile once, run many times.

Usage::

    engine = Engine(loaded)          # compile + build graph (once)
    result = engine.run("message")   # invoke (per request)
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from agentplatform.compiler import compile_spec
from agentplatform.runtime.langgraph_builder import build_langgraph
from agentplatform.runtime.mcp_manager import MCPClientFactory, MCPManager
from agentplatform.runtime.tool_models import ToolUsageRecord
from agentplatform.runtime.tool_registry import LocalToolProvider, MCPToolProvider, ToolRegistry
from agentplatform.spec.loader import LoadedSpec

T = TypeVar("T")


@dataclass(frozen=True)
class RunResult:
    """The outcome of one agent run."""

    system_name: str
    visited: list[str]
    answer: str
    used_tools: tuple[ToolUsageRecord, ...] = ()


class EngineRunError(RuntimeError):
    """Raised when a graph run fails after collecting partial run metadata."""

    def __init__(self, message: str, *, used_tools: tuple[ToolUsageRecord, ...]) -> None:
        super().__init__(message)
        self.used_tools = used_tools


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
        self._mcp_loop: _EngineAsyncLoop | None = None

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

        Currently, this starts MCP clients. MCP SDK transports keep async
        resources open, so start/stop and later MCP tool calls must use the same
        event loop.
        """
        if self._mcp_loop is not None:
            return

        loop = _EngineAsyncLoop()
        try:
            loop.run(self._mcp_manager.start())
        except Exception:
            loop.close()
            raise

        self._mcp_loop = loop

    def stop(self) -> None:
        """Stop long-lived runtime infrastructure."""
        loop = self._mcp_loop
        self._mcp_loop = None

        if loop is None:
            return

        try:
            loop.run(self._mcp_manager.stop())
        finally:
            loop.close()

    def run(self, message: str) -> RunResult:
        """Invoke the agent system with *message* and return the result."""
        used_tools: list[ToolUsageRecord] = []
        input_state: dict[str, object] = {"message": message, "used_tools": used_tools}
        try:
            result = self._app.invoke(cast(Any, input_state))
        except Exception as exc:
            raise EngineRunError(str(exc), used_tools=tuple(used_tools)) from exc

        return RunResult(
            system_name=self._system_name,
            visited=result.get("visited", []),
            answer=result.get("answer", ""),
            used_tools=tuple(result.get("used_tools", used_tools)),
        )


class _EngineAsyncLoop:
    """Run long-lived async runtime resources on one dedicated event loop."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_forever,
            name="agentplatform-engine-async-loop",
            daemon=True,
        )
        self._thread.start()

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    def _run_forever(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
