from __future__ import annotations

import importlib.util
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol, TypeVar

logger = logging.getLogger(__name__)


class Filterable(Protocol):
    """The minimal view a RouteFilter needs of a routing candidate.

    Decouples filters from the spec layer (``GraphNode``) and the runtime layer
    (node callables): a filter only ever inspects a candidate's id and whether
    it is protected. Declared read-only so frozen dataclasses satisfy it.
    """

    @property
    def id(self) -> str: ...

    @property
    def protected(self) -> bool: ...


T = TypeVar("T", bound=Filterable)


class RouteFilter(ABC):
    """Filters routing candidates before the orchestrator LLM makes a decision.

    Implement this interface to add cross-cutting concerns (access control,
    feature flags, rate limiting, etc.) without touching the engine core.
    Filters run in order; each receives the list returned by the previous one.
    """

    @abstractmethod
    def filter(self, ctx: dict[str, Any], candidates: list[T]) -> list[T]: ...


class AccessFilter(RouteFilter):
    """Removes protected nodes the caller is not allowed to reach."""

    def __init__(self, base_dir: Path) -> None:
        self._resolver = _load_access_resolver(base_dir)

    def filter(self, ctx: dict[str, Any], candidates: list[T]) -> list[T]:
        return [c for c in candidates if not c.protected or self._can_access(ctx, c.id)]

    def _can_access(self, ctx: dict[str, Any], node_id: str) -> bool:
        """Ask the resolver, failing closed: a raising resolver hides the node.

        The documented contract (docs/SIDECAR_CONTEXT_AUTH.md) is that a
        protected node is hidden when ``can_access`` returns false *or raises*.
        Letting the exception propagate would fail the whole run — or worse,
        an unimplemented resolver stub would crash instead of denying access.
        """
        try:
            return bool(self._resolver.can_access(ctx, node_id))
        except Exception:
            logger.warning(
                "access resolver raised for protected node '%s'; hiding it (fail closed)",
                node_id,
                exc_info=True,
            )
            return False


def _load_access_resolver(base_dir: Path) -> Any:
    path = base_dir / "plugins" / "access.py"
    spec = importlib.util.spec_from_file_location("access", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, "AccessResolver", None)
    if cls is None:
        raise ImportError(f"{path} must define class AccessResolver")
    return cls()
