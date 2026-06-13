"""RuntimeTool to LangChain tool adapter behavior."""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from agent_engine.runtime.context import ExecutionContext
from agent_engine.runtime.langchain_tool_adapter import (
    LangChainToolAdapterError,
    build_langchain_tools_from_runtime_tools,
    ensure_no_duplicate_tool_names,
)
from agent_engine.runtime.tool_models import RuntimeTool


class FakeToolRegistry:
    def __init__(self, result: object = "ok", *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[tuple[str, str, dict[str, object], ExecutionContext]] = []

    async def call_tool(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, object],
        ctx: ExecutionContext,
    ) -> object:
        self.calls.append((agent_id, tool_name, arguments, ctx))
        if self.fail:
            raise RuntimeError("registry failed")
        return self.result


def _runtime_tool(name: str = "flights_search") -> RuntimeTool:
    return RuntimeTool(
        name=name,
        description="Search flights",
        parameters_schema={
            "type": "object",
            "properties": {"origin": {"type": "string"}},
            "required": ["origin"],
        },
    )


def _build_tool(
    registry: FakeToolRegistry,
    *,
    ctx: ExecutionContext | None = None,
    runtime_tool: RuntimeTool | None = None,
) -> BaseTool:
    tools = build_langchain_tools_from_runtime_tools(
        agent_id="domestic_flights_agent",
        runtime_tools=[runtime_tool or _runtime_tool()],
        tool_registry=registry,
        ctx=ctx or ExecutionContext(message="hello", state={}),
    )
    return tools[0]


def test_runtime_tool_is_converted_to_langchain_tool() -> None:
    tool = _build_tool(FakeToolRegistry())

    assert isinstance(tool, BaseTool)


def test_tool_name_description_and_parameters_schema_are_preserved() -> None:
    runtime_tool = _runtime_tool()
    tool = _build_tool(FakeToolRegistry(), runtime_tool=runtime_tool)

    assert tool.name == runtime_tool.name
    assert tool.description == runtime_tool.description
    assert tool.args_schema == runtime_tool.parameters_schema


def test_invoking_tool_delegates_to_registry_with_context() -> None:
    registry = FakeToolRegistry(result="found")
    ctx = ExecutionContext(message="hello", state={"request": "state"})
    tool = _build_tool(registry, ctx=ctx)

    result = tool.invoke({"origin": "TLV"})

    assert result == "found"
    assert registry.calls == [("domestic_flights_agent", "flights_search", {"origin": "TLV"}, ctx)]


async def test_async_tool_invocation_delegates_to_registry() -> None:
    registry = FakeToolRegistry(result="found")
    ctx = ExecutionContext(message="hello", state={})
    tool = _build_tool(registry, ctx=ctx)

    result = await tool.ainvoke({"origin": "TLV"})

    assert result == "found"
    assert registry.calls == [("domestic_flights_agent", "flights_search", {"origin": "TLV"}, ctx)]


async def test_sync_tool_invocation_bridges_async_registry_inside_running_loop() -> None:
    registry = FakeToolRegistry(result="found")
    tool = _build_tool(registry)

    result = tool.invoke({"origin": "TLV"})

    assert result == "found"
    assert registry.calls[0][2] == {"origin": "TLV"}


def test_dict_results_are_normalized_as_stable_json() -> None:
    tool = _build_tool(FakeToolRegistry(result={"z": 2, "a": 1}))

    assert tool.invoke({"origin": "TLV"}) == '{"a":1,"z":2}'


def test_list_results_are_normalized_as_stable_json() -> None:
    tool = _build_tool(FakeToolRegistry(result=[{"z": 2, "a": 1}]))

    assert tool.invoke({"origin": "TLV"}) == '[{"a":1,"z":2}]'


def test_string_results_are_returned_directly() -> None:
    tool = _build_tool(FakeToolRegistry(result="plain text"))

    assert tool.invoke({"origin": "TLV"}) == "plain text"


def test_registry_errors_are_surfaced_clearly() -> None:
    tool = _build_tool(FakeToolRegistry(fail=True))

    with pytest.raises(
        LangChainToolAdapterError,
        match="Tool 'flights_search' failed for agent 'domestic_flights_agent'",
    ):
        tool.invoke({"origin": "TLV"})


def test_duplicate_runtime_tool_names_fail_before_binding() -> None:
    with pytest.raises(
        LangChainToolAdapterError,
        match="duplicate runtime tool name 'flights_search'",
    ):
        build_langchain_tools_from_runtime_tools(
            agent_id="domestic_flights_agent",
            runtime_tools=[_runtime_tool(), _runtime_tool()],
            tool_registry=FakeToolRegistry(),
            ctx=ExecutionContext(message="hello", state={}),
        )


def test_duplicate_local_and_runtime_tool_names_fail_before_binding() -> None:
    def local_tool(**kwargs: object) -> str:
        return "local"

    local = StructuredTool.from_function(
        local_tool,
        name="flights_search",
        description="Local tool",
    )

    with pytest.raises(
        LangChainToolAdapterError,
        match="Agent 'domestic_flights_agent' cannot bind duplicate tool name 'flights_search'",
    ):
        ensure_no_duplicate_tool_names(
            agent_id="domestic_flights_agent",
            local_tools=[local],
            runtime_tools=[_runtime_tool()],
        )
