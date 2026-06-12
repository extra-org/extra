from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from agentplatform.runtime.context import ExecutionContext
from agentplatform.runtime.mcp_manager import MCPManager
from agentplatform.runtime.tool_models import RuntimeTool, RuntimeToolBinding
from agentplatform.spec.models import AgentEngineSpec


class ToolRegistryError(RuntimeError):
    """Raised when runtime tool lookup or execution fails."""


class ToolProvider(Protocol):
    provider_id: str

    def get_tool_bindings_for_agent(self, agent_id: str) -> list[RuntimeToolBinding]: ...

    async def call_tool(
        self,
        *,
        agent_id: str,
        binding: RuntimeToolBinding,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object: ...


class MCPToolProvider(ToolProvider):
    provider_id = "mcp"

    def __init__(
        self,
        *,
        spec: AgentEngineSpec,
        mcp_manager: MCPManager,
    ) -> None:
        self._spec = spec
        self._mcp_manager = mcp_manager

    def get_tool_bindings_for_agent(self, agent_id: str) -> list[RuntimeToolBinding]:
        if agent_id not in self._spec.agents:
            raise ToolRegistryError(f"Unknown agent '{agent_id}'.")

        agent = self._spec.agents[agent_id]
        bindings: list[RuntimeToolBinding] = []

        for server_id in agent.mcps:
            for mcp_tool in self._mcp_manager.list_tools(server_id):
                runtime_tool = RuntimeTool(
                    name=mcp_tool.name,
                    description=mcp_tool.description,
                    parameters_schema=mcp_tool.parameters_schema,
                )
                bindings.append(
                    RuntimeToolBinding(
                        tool=runtime_tool,
                        provider_id=self.provider_id,
                        internal_tool_name=mcp_tool.name,
                        server_id=server_id,
                    )
                )

        return bindings

    async def call_tool(
        self,
        *,
        agent_id: str,
        binding: RuntimeToolBinding,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        if binding.server_id is None:
            raise ToolRegistryError(f"MCP tool '{binding.tool.name}' has no MCP server binding.")

        return await self._mcp_manager.call_tool(
            server_id=binding.server_id,
            tool_name=binding.internal_tool_name,
            arguments=arguments,
            ctx=ctx,
        )


class LocalToolProvider(ToolProvider):
    provider_id = "local"

    def __init__(self, *, spec: AgentEngineSpec) -> None:
        self._spec = spec

    def get_tool_bindings_for_agent(self, agent_id: str) -> list[RuntimeToolBinding]:
        if agent_id not in self._spec.agents:
            raise ToolRegistryError(f"Unknown agent '{agent_id}'.")

        agent = self._spec.agents[agent_id]
        bindings: list[RuntimeToolBinding] = []

        for tool_id in agent.tools:
            tool_spec = self._spec.tools[tool_id]

            runtime_tool = RuntimeTool(
                name=tool_id,
                description=tool_spec.description,
                parameters_schema={},  # later: from schema/params
            )

            bindings.append(
                RuntimeToolBinding(
                    tool=runtime_tool,
                    provider_id=self.provider_id,
                    internal_tool_name=tool_id,
                    server_id=None,
                )
            )

        return bindings

    async def call_tool(
        self,
        *,
        agent_id: str,
        binding: RuntimeToolBinding,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        raise ToolRegistryError("Local Python tool execution is not implemented yet.")


class ToolRegistry(ToolProvider):
    """Generic runtime-facing tool registry.

    The runtime uses this abstraction instead of calling MCPManager directly.
    """

    def __init__(self, providers: Sequence[ToolProvider]) -> None:
        self._providers = providers

    def get_tool_bindings_for_agent(self, agent_id: str) -> list[RuntimeToolBinding]:
        by_name: dict[str, RuntimeToolBinding] = {}

        for provider in self._providers:
            for binding in provider.get_tool_bindings_for_agent(agent_id):
                name = binding.tool.name

                if name in by_name:
                    existing = by_name[name]
                    raise ToolRegistryError(
                        f"Duplicate runtime tool name '{name}' exposed by "
                        f"'{existing.provider_id}' and '{binding.provider_id}'."
                    )

                by_name[name] = binding

        return list(by_name.values())

    def get_tools_for_agent(self, agent_id: str) -> list[RuntimeTool]:
        return [binding.tool for binding in self.get_tool_bindings_for_agent(agent_id)]

    async def call_tool(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        bindings = {
            binding.tool.name: binding for binding in self.get_tool_bindings_for_agent(agent_id)
        }

        binding = bindings.get(tool_name)
        if binding is None:
            raise ToolRegistryError(f"Tool '{tool_name}' is not allowed for agent '{agent_id}'.")

        for provider in self._providers:
            if provider.provider_id == binding.provider_id:
                return await provider.call_tool(
                    agent_id=agent_id,
                    binding=binding,
                    arguments=arguments,
                    ctx=ctx,
                )

        raise ToolRegistryError(
            f"No provider found for tool '{tool_name}' with provider id '{binding.provider_id}'."
        )
