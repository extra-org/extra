from __future__ import annotations


class SharedResolver:
    def __init__(self) -> None:
        pass

    def current_date(self, ctx: dict) -> str:
        """Returns the value for {{current_date}}"""
        return "2026-07-19"

    def user_name(self, ctx: dict) -> str:
        """Returns the value for {{user_name}}"""
        return "Tester"
