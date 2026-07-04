"""HookManager — runs custom enterprise code at fixed runtime lifecycle points.

Hooks are **not tools**: the LLM never sees them, cannot name them, and cannot
call them. They are trusted application code the runtime executes automatically.

The manager owns sync/async bridging: a hook may be a plain function, an async
function, a callable object, or a method on a long-lived hook instance, and
every ``run_*`` method awaits it uniformly. Because the whole runtime is async,
no ``asyncio.run`` is needed here.

Error policy (fail-closed by default, per hook ``failure_policy``):
  * ``on_engine_start`` failure   -> build/start fails.
  * ``on_engine_stop`` failure    -> logged; cleanup still proceeds.
  * ``on_run_start`` failure      -> the run fails.
  * ``on_run_end`` failure        -> the run's success path fails.
  * ``before_tool_call`` failure  -> the run fails.
  * ``after_tool_call`` failure   -> the run fails (use ``failure_policy: warn``
    for best-effort audit hooks).
  * ``transform_tool_result`` fail-> the run fails (use ``failure_policy: warn``
    to pass the original, untransformed result through instead).
  * ``on_tool_error`` failure     -> the run fails (use ``failure_policy: warn``).
  * ``before_mcp_request`` fail   -> the MCP request fails.
  * ``after_mcp_response`` fail   -> the MCP request fails (observe-only payload).
  * ``on_run_error`` failure      -> logged; the original run error is preserved.

A hook with ``failure_policy: warn`` is logged and skipped on failure instead of
aborting.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_engine.runtime.hooks.errors import HookExecutionError, HookLoadError
from agent_engine.runtime.hooks.loader import HookLoader
from agent_engine.runtime.hooks.models import (
    HOOK_POINTS,
    EngineContext,
    HookInvocation,
    HookPoint,
    McpRequestContext,
    McpResponseContext,
    RunContext,
    RunEndContext,
    ToolCallContext,
    ToolRequestContext,
    ToolResultContext,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedHook:
    """A resolved hook ready to run, with its declared policy."""

    point: HookPoint
    ref: str
    func: Any
    failure_policy: str = "fail"
    plugin: str | None = None
    method: str | None = None
    event_mode: bool = False


class HookManager:
    """Holds the loaded hooks per point and executes them in declaration order."""

    def __init__(self, hooks: Mapping[HookPoint, list[LoadedHook]] | None = None) -> None:
        self._hooks: dict[HookPoint, list[LoadedHook]] = {p: [] for p in HOOK_POINTS}
        for point, loaded in (hooks or {}).items():
            self._hooks[point] = list(loaded)
        total = sum(len(v) for v in self._hooks.values())
        logger.info("HookManager initialized hooks=%d", total)

    @classmethod
    def empty(cls) -> HookManager:
        """Return a manager with no registered hooks.

        An empty manager is still a real runtime collaborator: all execution
        methods are no-ops, and its presence means the engine was built.
        """
        return cls()

    @classmethod
    def from_config(
        cls,
        config: Any,
        loader: HookLoader | None = None,
        *,
        manifest_path: Path | None = None,
    ) -> HookManager:
        """Build a manager from a ``HooksConfig`` by importing every hook ref.

        Loading failures are fail-closed: a bad ref aborts construction, which in
        turn aborts ``Engine.build`` before any request is served.
        """
        if config is None:
            return cls.empty()

        loader = loader or HookLoader()
        specs = tuple(getattr(config, "hooks", ()))
        if not specs:
            return cls.empty()

        # Imported lazily to avoid an import cycle with generate.manifest, which
        # imports runtime.hooks.models. The manifest is read only when managed
        # plugin/method hooks are actually declared.
        needs_plugin_manifest = any(not spec.ref for spec in specs)
        plugin_refs: dict[str, str] = {}
        if needs_plugin_manifest and manifest_path is not None:
            from agent_engine.generate.manifest import hook_plugin_refs

            plugin_refs = hook_plugin_refs(manifest_path)
        plugin_instances: dict[str, object] = {}
        grouped: dict[HookPoint, list[LoadedHook]] = {p: [] for p in HOOK_POINTS}
        for spec in specs:
            if spec.ref:
                ref = spec.ref
                func = loader.load(spec.point, ref)
                event_mode = False
            else:
                plugin = spec.plugin or ""
                method = spec.method or ""
                class_ref = plugin_refs.get(plugin)
                if class_ref is None:
                    raise HookLoadError(
                        spec.point,
                        plugin,
                        f"hook plugin '{plugin}' is not declared in [hooks.plugins]",
                    )
                ref = f"{plugin}:{method}"
                func = loader.load_plugin_method(
                    spec.point,
                    plugin,
                    method,
                    class_ref,
                    plugin_instances,
                )
                event_mode = True
            grouped[spec.point].append(
                LoadedHook(
                    point=spec.point,
                    ref=ref,
                    func=func,
                    failure_policy=spec.failure_policy,
                    plugin=spec.plugin,
                    method=spec.method,
                    event_mode=event_mode,
                )
            )
            logger.info("hook loaded point=%s ref=%s", spec.point, ref)
        return cls(grouped)

    def has(self, point: HookPoint) -> bool:
        return bool(self._hooks.get(point))

    @property
    def hook_count(self) -> int:
        """Total number of registered hook entries."""
        return sum(len(hooks) for hooks in self._hooks.values())

    # -- lifecycle execution -------------------------------------------------

    async def run_engine_start(self, context: EngineContext) -> None:
        for hook in self._hooks["on_engine_start"]:
            await self._invoke(hook, payload=context, positional=(context,))

    async def run_engine_stop(self, context: EngineContext) -> None:
        """Engine-stop hooks are best-effort: a failure is logged and never
        prevents resource cleanup during shutdown."""
        for hook in self._hooks["on_engine_stop"]:
            try:
                await self._invoke(hook, payload=context, positional=(context,))
            except Exception:  # cleanup must proceed regardless
                logger.exception(
                    "on_engine_stop hook failed (continuing shutdown) ref=%s", hook.ref
                )

    async def run_run_start(self, context: RunContext) -> RunContext:
        for hook in self._hooks["on_run_start"]:
            result = await self._invoke(
                hook,
                payload=context,
                run_context=context,
                positional=(context,),
            )
            if isinstance(result, RunContext):
                context = result
        return context

    async def run_run_end(self, run_context: RunContext | None, summary: RunEndContext) -> None:
        """Run-end hooks observe a successful completion; return is ignored."""
        for hook in self._hooks["on_run_end"]:
            await self._invoke(
                hook,
                payload=summary,
                run_context=run_context,
                positional=(run_context, summary),
            )

    async def run_before_tool_call(
        self, run_context: RunContext | None, request: ToolRequestContext
    ) -> None:
        """Pre-call tool hooks are observe-only in this version; return ignored."""
        for hook in self._hooks["before_tool_call"]:
            await self._invoke(
                hook,
                payload=request,
                run_context=run_context,
                positional=(run_context, request),
            )

    async def run_after_tool_call(
        self, run_context: RunContext | None, call: ToolCallContext
    ) -> None:
        for hook in self._hooks["after_tool_call"]:
            await self._invoke(
                hook,
                payload=call,
                run_context=run_context,
                positional=(run_context, call),
            )

    async def run_transform_tool_result(
        self, run_context: RunContext | None, result: ToolResultContext
    ) -> ToolResultContext:
        """Transform a successful tool result before it reaches the conversation.

        Mirrors ``run_before_mcp_request``: each hook may return a (possibly
        modified) ``ToolResultContext``; the returned value is fed to the next
        hook, so transforms compose in declaration order. A hook that returns
        ``None`` (e.g. a ``warn`` hook that failed) leaves the result unchanged.
        """
        for hook in self._hooks["transform_tool_result"]:
            transformed = await self._invoke(
                hook,
                payload=result,
                run_context=run_context,
                positional=(run_context, result),
            )
            if isinstance(transformed, ToolResultContext):
                result = transformed
        return result

    async def run_on_tool_error(
        self, run_context: RunContext | None, call: ToolCallContext
    ) -> None:
        """Tool-error hooks observe a failed tool call; return ignored."""
        for hook in self._hooks["on_tool_error"]:
            await self._invoke(
                hook,
                payload=call,
                run_context=run_context,
                positional=(run_context, call),
            )

    async def run_before_mcp_request(
        self, run_context: RunContext | None, request: McpRequestContext
    ) -> McpRequestContext:
        for hook in self._hooks["before_mcp_request"]:
            result = await self._invoke(
                hook,
                payload=request,
                run_context=run_context,
                positional=(run_context, request),
            )
            if isinstance(result, McpRequestContext):
                request = result
        return request

    async def run_after_mcp_response(
        self, run_context: RunContext | None, response: McpResponseContext
    ) -> None:
        """Post-response MCP hooks are observe-only (audit/metrics); return ignored."""
        for hook in self._hooks["after_mcp_response"]:
            await self._invoke(
                hook,
                payload=response,
                run_context=run_context,
                positional=(run_context, response),
            )

    async def run_run_error(self, run_context: RunContext | None, error: BaseException) -> None:
        """Run error hooks best-effort: a hook failure here never masks ``error``."""
        for hook in self._hooks["on_run_error"]:
            try:
                await self._invoke(
                    hook,
                    payload=error,
                    run_context=run_context,
                    positional=(run_context, error),
                )
            except Exception:  # preserve the original run error above all
                logger.exception(
                    "on_run_error hook failed (original error preserved) ref=%s", hook.ref
                )

    # -- internals -----------------------------------------------------------

    async def _invoke(
        self,
        hook: LoadedHook,
        *,
        payload: object,
        positional: tuple[Any, ...],
        run_context: RunContext | None = None,
    ) -> Any:
        logger.info("hook start point=%s ref=%s", hook.point, hook.ref)
        start = time.perf_counter()
        try:
            if hook.event_mode:
                result = hook.func(
                    HookInvocation(
                        hook_point=hook.point,
                        plugin=hook.plugin,
                        method=hook.method,
                        ref=None,
                        run_context=run_context,
                        payload=payload,
                    )
                )
            else:
                result = hook.func(*positional)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "hook failed point=%s ref=%s ms=%d policy=%s err=%s",
                hook.point,
                hook.ref,
                duration_ms,
                hook.failure_policy,
                exc,
            )
            if hook.failure_policy == "warn":
                return None
            raise HookExecutionError(hook.point, hook.ref, exc) from exc
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info("hook done point=%s ref=%s ms=%d", hook.point, hook.ref, duration_ms)
        return result
