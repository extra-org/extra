"""Values injected into prompts as {{name}}.

One method per resolver id declared in agents.yaml. Each takes the run context
and returns a value. Instantiated once, so anything expensive belongs in
__init__ rather than in the methods.
"""

from __future__ import annotations

import os
from datetime import date


class SharedResolver:
    def __init__(self) -> None:
        self._bank_name = os.getenv("BANK_NAME", "Northwind Bank")

    def bank_name(self, ctx: dict) -> str:
        return self._bank_name

    def customer_name(self, ctx: dict) -> str:
        # A real deployment would look this up from the identity in ctx.
        # ctx carries run_id, conversation_id, user_id and auth details.
        return str(ctx.get("user_id") or "Dana")

    def today(self, ctx: dict) -> str:
        return date.today().isoformat()
