from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.field}] {self.message}"


class ParseError(Exception):
    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors = errors
        lines = "\n".join(f"  {e}" for e in errors)
        super().__init__(f"Parse failed:\n{lines}")
