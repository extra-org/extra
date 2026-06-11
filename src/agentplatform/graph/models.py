"""Typed models for the compiled agent graph.

These are the *output* of the compiler (``compiler/``) and the only models the
runtime is allowed to touch — never raw YAML (see ADR 0002).

Two distinct concepts (see ADR 0006):

- ``NodeDeclaration`` — *what a node is*. One per id under ``orchestrators`` or
  ``agents``. Reusable and shared.
- ``GraphInstance`` — *one occurrence of a node inside the* ``graph`` *tree*.
  Distinct per occurrence, with a stable ``instance_id`` and a pointer back to
  its shared ``NodeDeclaration``.

All models are frozen (immutable) and use tuples rather than lists so the
compiled graph cannot be mutated at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agentplatform.spec.models import McpSpec, ModelSpec, PromptSpec, ToolSpec

NodeType = Literal["orchestrator", "agent"]


@dataclass(frozen=True)
class ResolvedResolver:
    """A resolved resolver id.

    Resolvers are declared as a plain list of ids in the YAML (no further
    config); the runtime discovers the implementation from
    ``plugins/resolvers/{id}.py`` at build time.
    """

    id: str


@dataclass(frozen=True)
class ResolvedTool:
    """A tool reference resolved to its declaration."""

    id: str
    spec: ToolSpec


@dataclass(frozen=True)
class ResolvedMcp:
    """An MCP-server reference resolved to its declaration."""

    id: str
    spec: McpSpec


@dataclass(frozen=True)
class NodeDeclaration:
    """What a node *is* — a reusable, resolved declaration.

    References (``resolvers``/``tools``/``mcps``) are resolved to direct typed
    links. ``model`` already has defaults applied (``None`` only if neither a
    default nor an override was provided).
    """

    node_id: str
    node_type: NodeType
    description: str
    model: ModelSpec | None
    prompts: PromptSpec | None
    resolvers: tuple[ResolvedResolver, ...]
    tools: tuple[ResolvedTool, ...]
    mcps: tuple[ResolvedMcp, ...]
    protected: bool


@dataclass(frozen=True)
class GraphInstance:
    """One occurrence of a node inside the ``graph`` tree.

    ``instance_id`` is stable and unique per occurrence (so a node id reused in
    two graph locations yields two distinct instances). ``declaration`` is the
    shared ``NodeDeclaration`` this occurrence points back to.
    """

    instance_id: str
    node_id: str
    node_type: NodeType
    parent_instance_id: str | None
    path: str
    declaration: NodeDeclaration
    children: tuple[GraphInstance, ...]


@dataclass(frozen=True)
class CompiledAgentGraph:
    """The fully compiled, immutable agent graph the runtime consumes."""

    system_name: str
    root: GraphInstance
    instances_by_id: Mapping[str, GraphInstance]
    declarations_by_id: Mapping[str, NodeDeclaration]
