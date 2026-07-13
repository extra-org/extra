"""Sensitive-argument masking for approval requests.

A single, isolated place that redacts obviously-sensitive values before tool
arguments are shown to a human or persisted with a pending approval. It is a
defense-in-depth safeguard — not a substitute for keeping credentials out of
tool arguments entirely.

The masker never mutates its input: it returns a deep copy with sensitive
values replaced, so the arguments actually handed to the tool are untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Substrings matched case-insensitively against argument keys. Covers the common
# credential-bearing keys; matching a substring catches variants like
# ``x_api_key`` or ``refreshToken`` without enumerating every spelling.
_SENSITIVE_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "access_token",
    "refresh_token",
    "private_key",
    "credential",
    "session",
    "cookie",
    "bearer",
)

REDACTED = "***redacted***"


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def mask_sensitive(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive values recursively redacted.

    Dictionaries are copied key-by-key; a sensitive key's value is replaced with
    :data:`REDACTED` regardless of its shape. Lists/tuples are traversed so
    secrets nested inside collections are still masked. Scalars pass through
    unchanged. The original object is never modified.
    """
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else mask_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    ):
        return [mask_sensitive(item) for item in value]
    return value


def mask_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Mask a tool-argument mapping, returning a new redacted ``dict``."""
    return {
        key: REDACTED if _is_sensitive_key(key) else mask_sensitive(value)
        for key, value in arguments.items()
    }
