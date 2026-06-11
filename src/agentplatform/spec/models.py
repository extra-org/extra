"""Typed models for the declarative agent configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GraphNode = dict[str, Any]


class StrictSpecModel(BaseModel):
    """Base model that mirrors the schema contract and rejects unknown keys."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SystemSpec(StrictSpecModel):
    name: str


class ModelSpec(StrictSpecModel):
    provider: str
    name: str
    temperature: float | None = None


class DefaultsSpec(StrictSpecModel):
    model: ModelSpec | None = None


class McpSpec(StrictSpecModel):
    url: str


class ToolSpec(StrictSpecModel):
    description: str


class PromptSpec(StrictSpecModel):
    orchestrator: str | None = None
    system: str | None = None
    user: str | None = None

    def paths(self) -> dict[str, str]:
        return {
            field: value
            for field in ("orchestrator", "system", "user")
            if (value := getattr(self, field)) is not None
        }


class OrchestratorSpec(StrictSpecModel):
    description: str
    prompts: PromptSpec
    name: str | None = None
    model: ModelSpec | None = None
    resolvers: list[str] = Field(default_factory=list)
    protected: bool = False


class AgentSpec(StrictSpecModel):
    description: str
    name: str | None = None
    model: ModelSpec | None = None
    resolvers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
    protected: bool = False
    prompts: PromptSpec | None = None


class AgentEngineSpec(StrictSpecModel):
    system: SystemSpec
    graph: GraphNode
    defaults: DefaultsSpec | None = None
    mcps: dict[str, McpSpec] = Field(default_factory=dict)
    tools: dict[str, ToolSpec] = Field(default_factory=dict)
    resolvers: list[str] = Field(default_factory=list)
    orchestrators: dict[str, OrchestratorSpec] = Field(default_factory=dict)
    agents: dict[str, AgentSpec] = Field(default_factory=dict)

    def node_ids(self) -> set[str]:
        return set(self.orchestrators) | set(self.agents)
