"""Typed models for the compiled agent graph.

These are the *output* of the compiler (``compiler/``) and the only models the
runtime is allowed to touch — never raw YAML (see ADR 0002).

Two distinct concepts (see ADR 0006):

- ``NodeDeclaration`` — *what a node is*. Base dataclass with all common
  fields.  Concrete subtypes are ``OrchestratorDeclaration`` and
  ``AgentDeclaration``.
- ``AgentNode`` — *one concrete node inside the compiled agent graph*.
  Distinct per graph location, with a stable ``node_path`` and a pointer back
  to its pre-compiled ``NodeDeclaration``.

The graph layer is fully decoupled from the spec layer — no spec types appear
here.  All spec fields are flattened or projected into graph-native types by
the compiler.

All models are frozen (immutable) and use tuples rather than lists so the
compiled graph cannot be mutated at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class ResolvedResolver:
    """A resolver reference resolved to its id."""

    id: str


@dataclass(frozen=True, kw_only=True)
class ResolvedTool:
    """A tool reference with its resolved description."""

    id: str
    description: str


@dataclass(frozen=True, kw_only=True)
class ResolvedMcp:
    """An MCP-server reference with its resolved endpoint URL."""

    id: str
    url: str


@dataclass(frozen=True, kw_only=True)
class NodeDeclaration:
    """Common fields shared by all compiled node types.

    Model config and prompt paths are stored flat — no nested spec objects.
    Subclass with :class:`OrchestratorDeclaration` or :class:`AgentDeclaration`;
    do not instantiate this class directly.
    """

    node_id: str
    description: str

    # Flat model config — all None when no model is configured.
    model_provider: str | None = None
    model_name: str | None = None
    model_temperature: float | None = None

    # Prompt file paths — common to all node types.
    system_prompt: str | None = None
    user_prompt: str | None = None

    resolvers: tuple[ResolvedResolver, ...] = ()
    protected: bool = False


@dataclass(frozen=True, kw_only=True)
class OrchestratorDeclaration(NodeDeclaration):
    """Compiled declaration for an orchestrator node.

    Orchestrators route messages to child nodes.  The ``orchestrator_prompt``
    path points to the instructions used by the routing LLM.
    """

    orchestrator_prompt: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgentDeclaration(NodeDeclaration):
    """Compiled declaration for a leaf agent node.

    Agents call an LLM (optionally with tools) and produce an answer.
    """

    tools: tuple[ResolvedTool, ...] = ()
    mcps: tuple[ResolvedMcp, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AgentNode:
    """One concrete node inside the compiled agent graph.

    ``node_path`` is stable and unique within the graph (so a node id reused in
    two graph locations yields two distinct nodes).  ``declaration`` points to
    the pre-compiled ``NodeDeclaration`` for this node.  ``child_nodes`` holds
    any child ``AgentNode`` objects below this node.
    """

    node_path: str
    node_id: str
    parent_node_path: str | None
    declaration: NodeDeclaration
    child_nodes: tuple[AgentNode, ...]


@dataclass(frozen=True, kw_only=True)
class CompiledAgentGraph:
    """The fully compiled, immutable agent graph the runtime consumes."""

    system_name: str
    root: AgentNode
    nodes_by_id: Mapping[str, AgentNode]
    declarations_by_id: Mapping[str, NodeDeclaration]
