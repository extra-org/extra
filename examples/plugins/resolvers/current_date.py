"""Resolver: current_date

Returns a value injected as {{ current_date }} in prompt templates.
"""
from __future__ import annotations

from datetime import date


def current_date() -> str:
    """Return today's date in YYYY-MM-DD format."""
    return date.today().isoformat()
