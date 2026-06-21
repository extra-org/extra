from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    name: str
    temperature: float | None = None


@dataclass(frozen=True)
class BasePromptSet:
    system: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class OrchestratorPromptSet(BasePromptSet):
    orchestrator: str = ""


@dataclass(frozen=True)
class ResolverSpec:
    id: str
    scope: str  # "shared" | "agent"


@dataclass(frozen=True)
class ToolSpec:
    id: str
    description: str


@dataclass(frozen=True)
class McpToolTagTransport:
    """How configured ``tool_tags`` are sent to an MCP server during discovery.

    MCP's ``tools/list`` and ``langchain-mcp-adapters`` have no native tag/filter
    argument, so a tag-aware server expects the selector at the transport layer.
    ``type`` is ``"header"`` (use ``header_name``) or ``"query_param"`` (use
    ``param_name``).
    """

    type: str
    header_name: str | None = None
    param_name: str | None = None


@dataclass(frozen=True)
class MCPSpec:
    id: str
    url: str
    auth: bool = False  # if True, agentctl generate creates plugins/mcp_auth/{id}.py
    # Optional, per-server tool-discovery selector. Empty == no tags (unchanged
    # behavior). When non-empty, ``tool_tag_transport`` says how to send them.
    tool_tags: tuple[str, ...] = ()
    tool_tag_transport: McpToolTagTransport | None = None


@dataclass(frozen=True)
class NodeSpec(ABC):
    id: str
    name: str
    description: str
    model: ModelConfig
    resolvers: tuple[ResolverSpec, ...] = field(default_factory=tuple)
    protected: bool = False

    @abstractmethod
    def get_prompts(self) -> BasePromptSet: ...


@dataclass(frozen=True)
class OrchestratorSpec(NodeSpec):
    prompts: OrchestratorPromptSet = field(default_factory=OrchestratorPromptSet)

    def get_prompts(self) -> OrchestratorPromptSet:
        return self.prompts


@dataclass(frozen=True)
class AgentSpec(NodeSpec):
    prompts: BasePromptSet = field(default_factory=BasePromptSet)
    tools: tuple[ToolSpec, ...] = field(default_factory=tuple)
    mcps: tuple[MCPSpec, ...] = field(default_factory=tuple)

    def get_prompts(self) -> BasePromptSet:
        return self.prompts


@dataclass(frozen=True)
class GraphNode:
    node: NodeSpec
    children: tuple[GraphNode, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HookSpec:
    """One declared runtime hook: where it runs, what it is, how it behaves.

    ``point`` is a hook lifecycle point (e.g. "before_mcp_request"). A hook is
    declared either by explicit Python ``ref`` or by managed ``plugin`` +
    ``method`` resolved through plugins.toml. ``config`` is an opaque mapping
    passed to the hook invocation; ``failure_policy`` is "fail" (default,
    fail-closed) or "warn" (best-effort: log and continue on hook error).
    """

    point: str
    ref: str | None = None
    config: dict[str, object] = field(default_factory=dict)
    failure_policy: str = "fail"
    plugin: str | None = None
    method: str | None = None


@dataclass(frozen=True)
class HooksConfig:
    """All hooks declared for a system, in declaration order per point."""

    hooks: tuple[HookSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PluginsConfig:
    """Plugin loading configuration.

    ``import_roots`` are directories to put on ``sys.path`` so package-path
    plugin refs (e.g. ``examples.plugins.hooks.x:fn``) import reliably. Each is
    resolved relative to the agent YAML file, not the shell's working directory.
    """

    import_roots: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SystemMeta:
    name: str


@dataclass(frozen=True)
class DefaultsConfig:
    model: ModelConfig


@dataclass(frozen=True)
class SystemSpec:
    meta: SystemMeta
    defaults: DefaultsConfig | None
    graph: GraphNode
    hooks: HooksConfig = field(default_factory=HooksConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
