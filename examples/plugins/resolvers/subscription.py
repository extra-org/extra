"""Resolver: subscription

Returns a value injected as {{ subscription }} in prompt templates.
In production this would be fetched from a user/billing service.
"""
from __future__ import annotations

import os


def subscription() -> str:
    """Return the current user's subscription tier."""
    return os.environ.get("DEMO_SUBSCRIPTION", "Free")
