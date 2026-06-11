"""Resolver: user_name

Returns a value injected as {{ user_name }} in prompt templates.
In production this would come from the auth context (e.g. JWT claims).
"""
from __future__ import annotations

import os


def user_name() -> str:
    """Return the current user's display name."""
    return os.environ.get("DEMO_USER_NAME", "Amit")
