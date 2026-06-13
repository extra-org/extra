"""Error types for specification loading and validation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    """A located, actionable validation issue."""

    path: str
    message: str

    def __str__(self) -> str:
        if self.path:
            return f"{self.path}: {self.message}"
        return self.message


class SpecError(Exception):
    """Base class for specification errors."""


class SpecLoadError(SpecError):
    """Raised when a YAML file cannot be read or parsed."""


class SpecSchemaError(SpecError):
    """Raised when raw YAML violates the JSON schema."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(_format_issue_message("JSON schema validation failed", issues))


class SpecValidationError(SpecError):
    """Raised when semantic validation fails."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(_format_issue_message("Semantic validation failed", issues))


def _format_issue_message(heading: str, issues: list[ValidationIssue]) -> str:
    if not issues:
        return heading
    formatted = "\n".join(f"- {issue}" for issue in issues)
    return f"{heading}:\n{formatted}"
