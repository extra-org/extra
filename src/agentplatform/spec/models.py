"""Typed models for the declarative agent configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The graph section is a tree: each key is a node_id, each value is that
# node's children subtree (another GraphChildren) or None for leaf nodes.
GraphChildren = dict[str, Any]


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


class ResolverSpec(StrictSpecModel):
    scope: str = "agent"
    return_type: str = "str"

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        if value not in ("agent", "shared"):
            raise ValueError(f"Invalid resolver scope '{value}'. Must be 'agent' or 'shared'.")
        return value


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
    graph: GraphChildren
    defaults: DefaultsSpec | None = None
    mcps: dict[str, McpSpec] = Field(default_factory=dict)
    tools: dict[str, ToolSpec] = Field(default_factory=dict)
    resolvers: dict[str, ResolverSpec] = Field(default_factory=dict)
    orchestrators: dict[str, OrchestratorSpec] = Field(default_factory=dict)
    agents: dict[str, AgentSpec] = Field(default_factory=dict)

    @field_validator("resolvers", mode="before")
    @classmethod
    def _normalize_resolvers(cls, value: object) -> object:
        if isinstance(value, list):
            return {resolver_id: {} for resolver_id in value}
        if isinstance(value, dict):
            return {
                resolver_id: ({} if resolver_spec is None else resolver_spec)
                for resolver_id, resolver_spec in value.items()
            }
        return value

    def node_ids(self) -> set[str]:
        return set(self.orchestrators) | set(self.agents)
