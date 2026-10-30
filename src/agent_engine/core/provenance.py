"""Prompt provenance models and deterministic content hashing.

Provides provider-agnostic provenance metadata tracking the source origin,
stable identifier, requested reference, immutable resolved revision, and
deterministic content fingerprint of prompt source templates.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any


def normalize_prompt_path(path: str | Path | PurePath) -> str:
    """Normalize a local prompt path to a canonical POSIX forward-slash string.

    Interprets backslash separators in relative string paths via PureWindowsPath
    while preserving Path and PurePath instances and avoiding reinterpreting
    anchored or drive-prefixed Windows paths as relative paths on POSIX.
    """
    if isinstance(path, PurePath):
        return path.as_posix()
    win_path = PureWindowsPath(path)
    if not win_path.drive and not win_path.root:
        return win_path.as_posix()
    return PurePath(path).as_posix()


def compute_prompt_hash(content: str) -> str:
    """Compute a deterministic SHA-256 fingerprint of prompt source text.

    Args:
        content: The raw prompt source string to hash.

    Returns:
        Deterministic fingerprint formatted as 'sha256:<64-char-hex-digest>'.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class PromptProvenance:
    """Provider-agnostic provenance metadata for a prompt source.

    Captures the origin and exact immutable revision or content fingerprint
    of a prompt template before runtime variable substitution.
    """

    source: str
    identifier: str
    content_hash: str
    requested_ref: str | None = None
    resolved_revision: str | None = None

    @classmethod
    def for_local_file(
        cls,
        path: str | Path | PurePath,
        content: str,
    ) -> PromptProvenance:
        """Create provenance for a local prompt file.

        The path identifier is normalized to POSIX forward-slash format to ensure
        consistent provenance identifiers across platforms.
        """
        normalized_identifier = normalize_prompt_path(path)
        return cls(
            source="file",
            identifier=normalized_identifier,
            content_hash=compute_prompt_hash(content),
            requested_ref=None,
            resolved_revision=None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the provenance metadata."""
        return {
            "source": self.source,
            "identifier": self.identifier,
            "requested_ref": self.requested_ref,
            "resolved_revision": self.resolved_revision,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class ResolvedPrompt:
    """A prompt template loaded from a source, paired with its provenance."""

    content: str
    provenance: PromptProvenance
