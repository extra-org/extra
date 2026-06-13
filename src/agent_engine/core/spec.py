from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    name: str
    temperature: Optional[float] = None


@dataclass(frozen=True)
class BasePromptSet:
    system: Optional[str] = None
    user: Optional[str] = None


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
class MCPSpec:
    id: str
    url: str


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
class SystemMeta:
    name: str


@dataclass(frozen=True)
class DefaultsConfig:
    model: ModelConfig


@dataclass(frozen=True)
class SystemSpec:
    meta: SystemMeta
    defaults: Optional[DefaultsConfig]
    graph: GraphNode
