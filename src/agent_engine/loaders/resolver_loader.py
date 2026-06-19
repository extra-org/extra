from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ResolverLoaderError(RuntimeError):
    pass


class ResolverLoader:
    """Loads per-agent resolver classes from plugins/resolvers/{agent_id}.py.

    Each file must contain a class named Resolver. The class is instantiated
    once per agent_id and cached — shared resources (DB connections, clients)
    are initialized in __init__ and reused across resolver calls.

    If plugins/resolvers/shared.py exists, it is loaded first and registered in
    sys.modules as "shared" so agent files can inherit from SharedResolver via
    `from shared import SharedResolver`.

    Resolver methods are named after resolver IDs and accept a single ctx dict.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._instances: dict[str, Any] = {}
        self._shared_loaded = False

    def load(self, agent_id: str, resolver_id: str) -> Callable[[dict[str, Any]], Any]:
        instance = self._get_or_create(agent_id)
        method = getattr(instance, resolver_id, None)
        if method is None or not callable(method):
            cls_name = type(instance).__name__
            raise ResolverLoaderError(
                f"Resolver class '{cls_name}' for agent '{agent_id}' has no method '{resolver_id}'"
            )
        return method

    def _get_or_create(self, agent_id: str) -> Any:
        if agent_id not in self._instances:
            self._instances[agent_id] = self._instantiate(agent_id)
        return self._instances[agent_id]

    def _instantiate(self, agent_id: str) -> Any:
        resolvers_dir = self._base_dir / "plugins" / "resolvers"
        self._ensure_shared_module(resolvers_dir)
        path = resolvers_dir / f"{agent_id}.py"
        if not path.is_file():
            raise ResolverLoaderError(
                f"Resolver plugin not found: {path}\nRun `agentctl generate` to create the stub."
            )
        module = _import_from_path(path)
        cls = getattr(module, "Resolver", None)
        if cls is None or not isinstance(cls, type):
            raise ResolverLoaderError(f"{path} must define a class named 'Resolver'")
        try:
            return cls()
        except Exception as exc:
            raise ResolverLoaderError(
                f"Failed to instantiate Resolver for agent '{agent_id}': {exc}"
            ) from exc

    def _ensure_shared_module(self, resolvers_dir: Path) -> None:
        """Load shared.py once and register it as sys.modules['shared'].

        This lets agent resolver files do `from shared import SharedResolver`
        without needing shared.py on the Python path.
        """
        if self._shared_loaded:
            return
        self._shared_loaded = True
        shared_path = resolvers_dir / "shared.py"
        if shared_path.is_file():
            module = _import_from_path(shared_path)
            sys.modules.setdefault("shared", module)


def _import_from_path(path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
