"""Loads hook callables from import-path references.

Canonical ref format is ``module.submodule:attribute`` — the colon makes the
module/attribute boundary unambiguous. The dotted form ``module.submodule.attr``
is also accepted (the last segment is treated as the attribute) for convenience.

A ref may point to:
  * a function (sync or async);
  * a callable object (anything with ``__call__``);
  * a class — it is instantiated with no arguments and the instance, which must
    be callable, is returned.
  * a class method, written ``module:Class.method`` — the class is instantiated
    once during loading and the bound method is returned.

Hooks are *trusted application code*. The loader imports normal Python modules
from the host application's environment; it does not sandbox them.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Callable
from functools import update_wrapper
from typing import Any

from agent_engine.runtime.hooks.errors import HookLoadError

logger = logging.getLogger(__name__)


class HookLoader:
    def load(self, point: str, ref: str) -> Callable[..., Any]:
        module_path, attr_path = self._split(point, ref)
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # ImportError and anything raised at import time
            raise HookLoadError(point, ref, f"cannot import '{module_path}': {exc}") from exc

        target = self._resolve_target(point, ref, module_path, module, attr_path)

        if not callable(target):
            raise HookLoadError(point, ref, f"'{ref}' is not callable")
        logger.debug(
            "hook loaded point=%s ref=%s module=%s attr=%s",
            point,
            ref,
            module_path,
            attr_path,
        )
        return target

    def load_plugin_method(
        self,
        point: str,
        plugin_id: str,
        method_name: str,
        class_ref: str,
        instances: dict[str, object],
    ) -> Callable[..., Any]:
        """Load a managed hook plugin method, reusing one instance per plugin id."""
        if plugin_id not in instances:
            module_path, class_name = self._split(point, class_ref)
            try:
                module = importlib.import_module(module_path)
            except Exception as exc:
                raise HookLoadError(
                    point,
                    plugin_id,
                    f"cannot import hook plugin '{plugin_id}' module '{module_path}': {exc}",
                ) from exc
            cls = getattr(module, class_name, None)
            if cls is None:
                raise HookLoadError(
                    point,
                    plugin_id,
                    f"hook plugin '{plugin_id}' class '{class_name}' was not found",
                )
            if not inspect.isclass(cls):
                raise HookLoadError(
                    point,
                    plugin_id,
                    f"hook plugin '{plugin_id}' target '{class_name}' is not a class",
                )
            instances[plugin_id] = self._instantiate_class(point, plugin_id, cls)

        instance = instances[plugin_id]
        method = getattr(instance, method_name, None)
        if method is None:
            raise HookLoadError(
                point,
                plugin_id,
                f"hook plugin '{plugin_id}' has no method '{method_name}'",
            )
        if not callable(method):
            raise HookLoadError(
                point,
                plugin_id,
                f"hook plugin '{plugin_id}' method '{method_name}' is not callable",
            )

        def invoke(event: Any) -> Any:
            return method(event)

        update_wrapper(invoke, method)
        invoke.__agent_hook_instance__ = instance  # type: ignore[attr-defined]
        return invoke

    def _resolve_target(
        self,
        point: str,
        ref: str,
        module_path: str,
        module: Any,
        attr_path: str,
    ) -> Callable[..., Any]:
        parts = attr_path.split(".")
        if len(parts) == 1:
            attr = parts[0]
            target = getattr(module, attr, None)
            if target is None:
                raise HookLoadError(point, ref, f"'{module_path}' has no attribute '{attr}'")
            if inspect.isclass(target):
                target = self._instantiate_class(point, ref, target)
            return target

        if len(parts) != 2:
            raise HookLoadError(
                point,
                ref,
                "ref attribute must be 'attribute' or 'Class.method'",
            )

        class_name, method_name = parts
        if not class_name or not method_name:
            raise HookLoadError(
                point,
                ref,
                "ref attribute must be 'attribute' or 'Class.method'",
            )
        cls = getattr(module, class_name, None)
        if cls is None:
            raise HookLoadError(point, ref, f"'{module_path}' has no class '{class_name}'")
        if not inspect.isclass(cls):
            raise HookLoadError(point, ref, f"'{module_path}.{class_name}' is not a class")
        instance = self._instantiate_class(point, ref, cls)
        method = getattr(instance, method_name, None)
        if method is None:
            raise HookLoadError(point, ref, f"class '{class_name}' has no method '{method_name}'")
        if not callable(method):
            raise HookLoadError(
                point,
                ref,
                f"'{class_name}.{method_name}' exists but is not callable",
            )

        def invoke(*args: Any) -> Any:
            return method(*args)

        update_wrapper(invoke, method)
        invoke.__agent_hook_instance__ = instance  # type: ignore[attr-defined]
        return invoke

    def _instantiate_class(self, point: str, ref: str, cls: type) -> Any:
        try:
            return cls()
        except TypeError as exc:
            raise HookLoadError(
                point,
                ref,
                f"invalid constructor for class '{cls.__name__}': {exc}",
            ) from exc
        except Exception as exc:
            raise HookLoadError(
                point,
                ref,
                f"could not instantiate class '{cls.__name__}': {exc}",
            ) from exc

    @staticmethod
    def _split(point: str, ref: str) -> tuple[str, str]:
        if ":" in ref:
            module_path, _, attr = ref.partition(":")
        elif "." in ref:
            module_path, _, attr = ref.rpartition(".")
        else:
            raise HookLoadError(point, ref, "ref must be 'module:attribute' or 'module.attribute'")
        if not module_path or not attr:
            raise HookLoadError(point, ref, "ref must name both a module and an attribute")
        return module_path, attr
