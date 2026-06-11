"""Runtime plugin loader.

Discovers tool and resolver implementations from the ``plugins/`` directory
that lives next to the agent YAML.  The layout is:

    plugins/
      tools/{tool_id}.py      — one file per tool; must export ``{tool_id}(...)``
      resolvers/{resolver_id}.py — one file per resolver; must export ``{resolver_id}()``

Run ``agentctl generate`` to create stubs for any new ids declared in the YAML.
"""

from __future__ import annotations

import importlib.util
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from agentplatform.utils import ProjectPaths


class PluginLoader:
    """Loads tool and resolver callables from a ``plugins/`` directory.

    Pass ``base_dir`` (the directory that contains ``agents.yml``); the loader
    derives ``plugins/tools/`` and ``plugins/resolvers/`` from there.
    """

    def __init__(self, base_dir: Path) -> None:
        self._paths = ProjectPaths(base_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_tool(self, tool_id: str, description: str) -> BaseTool:
        """Return a LangChain ``BaseTool`` backed by ``plugins/tools/{tool_id}.py``.

        The file must export a callable with the same name as ``tool_id``.
        The ``description`` from the YAML is used so the LLM knows when to call it.
        """
        fn = self._load_fn(self._paths.tool(tool_id), tool_id)
        return StructuredTool.from_function(fn, description=description)

    def load_resolver(self, resolver_id: str) -> Callable[[], Any]:
        """Return the resolver callable from ``plugins/resolvers/{resolver_id}.py``.

        The file must export a zero-argument callable with the same name as
        ``resolver_id``.  Its return value is injected as ``{{ resolver_id }}``
        in prompt templates.
        """
        return self._load_fn(self._paths.resolver(resolver_id), resolver_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _load_fn(path: Path, name: str) -> Callable[..., Any]:
        if not path.is_file():
            raise FileNotFoundError(
                f"Plugin file not found: {path}\n"
                f"Run `agentctl generate` to create the stub, then implement it."
            )
        module = PluginLoader._import_module(path)
        fn = getattr(module, name, None)
        if fn is None or not callable(fn):
            raise AttributeError(
                f"Plugin file {path} must export a callable named '{name}'."
            )
        return fn

    @staticmethod
    def _import_module(path: Path) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
