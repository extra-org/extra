"""Prompt-template loading and request-time placeholder rendering."""

from __future__ import annotations

import re
from pathlib import Path

from agent_engine.core.provenance import (
    PromptProvenance,
    ResolvedPrompt,
    compute_prompt_hash,
    normalize_prompt_path,
)
from agent_engine.core.spec import NodeSpec
from agent_engine.loaders.resolver_loader import ResolverLoader

__all__ = [
    "PromptProvenance",
    "ResolvedPrompt",
    "compute_prompt_hash",
    "load_file",
    "load_file_with_provenance",
    "render_prompt",
    "resolve_prompt_context",
]


def render_prompt(template: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return context.get(match.group(1).strip(), match.group(0))

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def resolve_prompt_context(loader: ResolverLoader, spec: NodeSpec) -> dict[str, str]:
    """Run a node's declared resolvers and return the accumulated key→value map.

    Resolvers run in declaration order, each seeing earlier values, and are
    never cached — a resolver may answer differently for each caller.
    """
    context: dict[str, str] = {}
    for resolver in spec.resolvers:
        context[resolver.id] = str(loader.load(spec.id, resolver.id)(context))
    return context


def load_file_with_provenance(
    base_dir: Path,
    relative_path: str | None,
) -> ResolvedPrompt | None:
    """Load a local prompt file and construct its provenance metadata.

    Returns:
        A ResolvedPrompt containing the raw file content and its provenance,
        or None if relative_path is empty or the file does not exist.
    """
    if not relative_path:
        return None
    normalized_path = normalize_prompt_path(relative_path)
    path = base_dir / normalized_path
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    provenance = PromptProvenance.for_local_file(normalized_path, content)
    return ResolvedPrompt(content=content, provenance=provenance)


def load_file(base_dir: Path, relative_path: str | None) -> str:
    """Load the raw text of a prompt template from disk.

    Backward-compatible convenience helper that discards provenance metadata.
    """
    resolved = load_file_with_provenance(base_dir, relative_path)
    return resolved.content if resolved is not None else ""
