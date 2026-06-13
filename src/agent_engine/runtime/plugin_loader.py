"""Runtime plugin loader.

Discovers tool and resolver implementations from the ``plugins/`` directory
that lives next to the agent YAML.  The layout is:

    plugins/
      tools/{tool_id}.py      — one file per tool; must export ``{tool_id}(...)``
      resolvers/resolvers.toml — maps agent ids to resolver classes
      resolvers/base.py        — shared resolver base class
      resolvers/{agent_id}.py  — customer-owned agent resolver class

Run ``agentctl generate`` to create stubs for any new ids declared in the YAML.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import tomllib
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from agent_engine.runtime.context import ExecutionContext
from agent_engine.utils import ProjectPaths


class ResolverPluginError(RuntimeError):
    """Raised when the resolver plugin class cannot be loaded or used."""


@dataclass(frozen=True)
class ResolverPluginConfig:
    base_class_path: str
    dependencies: dict[str, object]
    agent_class_paths: dict[str, str]


class PluginLoader:
    """Loads tool and resolver callables from a ``plugins/`` directory.

    Pass ``base_dir`` (the directory that contains ``agents.yml``); the loader
    derives ``plugins/tools/`` and ``plugins/resolvers/`` from there.
    """

    def __init__(self, base_dir: Path) -> None:
        self._paths = ProjectPaths(base_dir)
        self._resolver_config: ResolverPluginConfig | None = None
        self._resolver_base_class: type[Any] | None = None
        self._resolver_instances: dict[str, object] = {}
        self._resolver_modules: dict[str, types.ModuleType] = {}
        self._resolver_project_modules_cleared = False

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

    def load_resolver(
        self,
        agent_id: str,
        resolver_id: str,
        *,
        method_name: str | None = None,
    ) -> Callable[[ExecutionContext], Any]:
        """Return a bound resolver method from the configured agent resolver class.

        ``plugins/resolvers/resolvers.toml`` maps the selected ``agent_id`` to a
        resolver class. The class is instantiated once per loader and the YAML
        resolver method is looked up by name.
        """
        config = self._load_resolver_config()
        class_path = config.agent_class_paths.get(agent_id)
        if class_path is None:
            raise ResolverPluginError(
                f"Agent '{agent_id}' declares resolver '{resolver_id}', but "
                f"{self._paths.resolver_config} has no [resolvers.agents.{agent_id}] class."
            )

        method = method_name or resolver_id
        resolver = self._load_resolver_instance(agent_id, class_path, config)
        configured_class_name = class_path.rsplit(".", maxsplit=1)[-1]
        attr = getattr(resolver, method, None)
        if attr is None:
            raise ResolverPluginError(
                f"Resolver method '{method}' for resolver '{resolver_id}' was not found "
                f"on configured class '{configured_class_name}' for agent '{agent_id}'."
            )
        if not callable(attr):
            raise ResolverPluginError(
                f"Resolver attribute '{method}' for resolver '{resolver_id}' on "
                f"'{configured_class_name}' for agent '{agent_id}' exists but is not callable."
            )
        self._validate_resolver_signature(resolver_id, method, attr)
        return attr

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
            raise AttributeError(f"Plugin file {path} must export a callable named '{name}'.")
        return fn

    def _load_resolver_config(self) -> ResolverPluginConfig:
        if self._resolver_config is not None:
            return self._resolver_config

        path = self._paths.resolver_config
        if not path.is_file():
            raise ResolverPluginError(
                f"Resolver TOML config not found: {path}\n"
                f"Run `agentctl generate` to create {ProjectPaths.resolver_config_rel()}."
            )

        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ResolverPluginError(f"Invalid resolver TOML config {path}: {exc}") from exc

        resolvers = raw.get("resolvers")
        if not isinstance(resolvers, dict):
            raise ResolverPluginError(
                f"Resolver TOML config {path} must define a [resolvers] table."
            )

        base_class_path = resolvers.get("base_class")
        if not isinstance(base_class_path, str) or not base_class_path:
            raise ResolverPluginError(
                f"Resolver TOML config {path} must define resolvers.base_class."
            )
        if "." not in base_class_path or base_class_path.endswith("."):
            raise ResolverPluginError(
                f"Invalid base resolver class import path '{base_class_path}' in {path}."
            )

        dependencies = resolvers.get("dependencies", {})
        if not isinstance(dependencies, dict):
            raise ResolverPluginError(
                f"Resolver TOML config {path} must define [resolvers.dependencies] "
                "as a table when dependencies are provided."
            )

        self._resolver_config = ResolverPluginConfig(
            base_class_path=base_class_path,
            dependencies=dict(dependencies),
            agent_class_paths=self._load_agent_class_paths(path, resolvers),
        )
        return self._resolver_config

    @staticmethod
    def _load_agent_class_paths(
        path: Path,
        resolvers: dict[object, object],
    ) -> dict[str, str]:
        agents = resolvers.get("agents", {})
        if not isinstance(agents, dict):
            raise ResolverPluginError(
                f"Resolver TOML config {path} must define [resolvers.agents] as a table."
            )

        agent_class_paths: dict[str, str] = {}
        for agent_id, agent_config in agents.items():
            if not isinstance(agent_id, str):
                raise ResolverPluginError(f"Resolver TOML config {path} has a non-string agent id.")
            if not isinstance(agent_config, dict):
                raise ResolverPluginError(
                    f"Resolver TOML config {path} must define [resolvers.agents.{agent_id}] "
                    "as a table."
                )
            class_path = agent_config.get("class")
            if not isinstance(class_path, str) or not class_path:
                raise ResolverPluginError(
                    f"Resolver TOML config {path} must define resolvers.agents.{agent_id}.class."
                )
            if "." not in class_path or class_path.endswith("."):
                raise ResolverPluginError(
                    f"Invalid resolver class import path '{class_path}' for agent "
                    f"'{agent_id}' in {path}."
                )
            agent_class_paths[agent_id] = class_path
        return agent_class_paths

    def _load_resolver_instance(
        self,
        agent_id: str,
        class_path: str,
        config: ResolverPluginConfig,
    ) -> object:
        if agent_id in self._resolver_instances:
            return self._resolver_instances[agent_id]

        base_cls = self._load_base_resolver_class(config)
        cls = self._import_resolver_class(class_path)
        if not issubclass(cls, base_cls):
            raise ResolverPluginError(
                f"Resolver class '{class_path}' for agent '{agent_id}' must inherit "
                f"from base resolver class '{config.base_class_path}'."
            )
        try:
            instance = cls(**config.dependencies)
        except Exception as exc:
            raise ResolverPluginError(
                f"Resolver class '{class_path}' for agent '{agent_id}' could not be "
                f"instantiated: {exc}"
            ) from exc
        self._resolver_instances[agent_id] = instance
        return instance

    def _load_base_resolver_class(self, config: ResolverPluginConfig) -> type[Any]:
        if self._resolver_base_class is None:
            self._resolver_base_class = self._import_resolver_class(config.base_class_path)
        return self._resolver_base_class

    def _import_resolver_class(self, class_path: str) -> type[Any]:
        module_name, class_name = class_path.rsplit(".", maxsplit=1)
        added_path = False
        base_dir = str(self._paths.base_dir)
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)
            added_path = True
        try:
            if module_name.startswith("plugins.") and not self._resolver_project_modules_cleared:
                for loaded_module in list(sys.modules):
                    if loaded_module == "plugins" or loaded_module.startswith("plugins."):
                        sys.modules.pop(loaded_module, None)
                self._resolver_project_modules_cleared = True
            module = self._resolver_modules.get(module_name)
            if module is None:
                module = importlib.import_module(module_name)
                self._resolver_modules[module_name] = module
        except Exception as exc:
            raise ResolverPluginError(
                f"Resolver class '{class_path}' could not be imported: {exc}"
            ) from exc
        finally:
            if added_path:
                sys.path.remove(base_dir)

        obj = getattr(module, class_name, None)
        if obj is None:
            raise ResolverPluginError(
                f"Resolver class '{class_name}' was not found in module '{module_name}'."
            )
        if not isinstance(obj, type):
            raise ResolverPluginError(f"Resolver import path '{class_path}' is not a class.")
        return obj

    @staticmethod
    def _validate_resolver_signature(
        resolver_id: str,
        method_name: str,
        method: Callable[..., Any],
    ) -> None:
        try:
            inspect.signature(method).bind(object())
        except TypeError as exc:
            raise ResolverPluginError(
                f"Resolver method '{method_name}' for resolver '{resolver_id}' must "
                "accept exactly one ctx argument."
            ) from exc

    @staticmethod
    def _import_module(path: Path) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
