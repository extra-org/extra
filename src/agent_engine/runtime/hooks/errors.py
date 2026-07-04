"""Errors raised while loading or executing runtime hooks.

Every error carries the hook *point* and *ref* so failures are traceable to the
exact entry in the YAML ``hooks`` section without including runtime payload or
credential material.
"""

from __future__ import annotations


class HookError(Exception):
    """Base class for all hook-related errors."""


class HookValidationError(HookError):
    """A hook declaration in the YAML is malformed (bad point, missing ref, ...)."""


class HookLoadError(HookError):
    """A hook ``ref`` could not be imported or is not callable."""

    def __init__(self, point: str, ref: str, reason: str) -> None:
        self.point = point
        self.ref = ref
        self.reason = reason
        super().__init__(f"Failed to load hook for '{point}' (ref='{ref}'): {reason}")


class HookExecutionError(HookError):
    """A hook raised while running. Wraps the original exception as ``__cause__``."""

    def __init__(self, point: str, ref: str, cause: BaseException) -> None:
        self.point = point
        self.ref = ref
        self.cause = cause
        super().__init__(f"Hook for '{point}' (ref='{ref}') failed: {cause}")
