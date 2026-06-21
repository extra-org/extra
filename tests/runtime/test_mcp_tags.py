"""Tests for optional MCP tool-discovery tags (tool_tags).

Covers the pure transport helper, parser/schema validation, and engine discovery
wiring. No real network: the MCP client is replaced with a capturing fake.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    MCPSpec,
    McpToolTagTransport,
    ModelConfig,
    SystemMeta,
    SystemSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.langgraph.helpers import collect_mcp_specs
from agent_engine.loaders.mcp_tags import (
    DEFAULT_TOOL_TAG_HEADER,
    McpToolTagError,
    apply_tool_tags,
    effective_tool_tag_transport,
)
from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


# -- pure helper -------------------------------------------------------------


def test_apply_no_tags_is_noop() -> None:
    config = {"url": "https://x/mcp", "transport": "streamable_http"}
    assert apply_tool_tags(dict(config), (), None) == config


def test_apply_header_transport() -> None:
    config = apply_tool_tags(
        {"url": "https://x/mcp"},
        ("invoices", "customers"),
        McpToolTagTransport("header", header_name="X-MCP-Tool-Tag"),
    )
    assert config["headers"] == {"X-MCP-Tool-Tag": "invoices,customers"}
    assert config["url"] == "https://x/mcp"  # url untouched for header transport


def test_apply_query_param_preserves_existing_query() -> None:
    config = apply_tool_tags(
        {"url": "https://x/mcp?foo=1"},
        ("invoices",),
        McpToolTagTransport("query_param", param_name="tag"),
    )
    assert config["url"] == "https://x/mcp?foo=1&tag=invoices"


def test_apply_tags_without_transport_uses_default_header() -> None:
    config = apply_tool_tags({"url": "https://x/mcp"}, ("invoices",), None, server_id="bc")
    assert config["headers"] == {DEFAULT_TOOL_TAG_HEADER: "invoices"}


def test_apply_multiple_tags_without_transport_comma_joined() -> None:
    config = apply_tool_tags({"url": "https://x/mcp"}, ("invoices", "customers"), None)
    assert config["headers"] == {DEFAULT_TOOL_TAG_HEADER: "invoices,customers"}


def test_default_header_constant() -> None:
    assert DEFAULT_TOOL_TAG_HEADER == "X-MCP-Tool-Tag"


def test_apply_unknown_transport_type_raises() -> None:
    with pytest.raises(McpToolTagError):
        apply_tool_tags({"url": "u"}, ("invoices",), McpToolTagTransport("cookie"))


# -- parser / schema ---------------------------------------------------------

_BASE = (
    "system:\n  name: t\nagents:\n  a:\n    description: d\n    mcps: [bc]\ngraph:\n  a:\nmcps:\n"
)


def _parse(body: str) -> SystemSpec:
    d = Path(tempfile.mkdtemp())
    (d / "a.yml").write_text(_BASE + body, encoding="utf-8")
    return YAMLParser().parse(str(d / "a.yml"))


def _mcp(spec: SystemSpec) -> MCPSpec:
    node = spec.graph.node
    assert isinstance(node, AgentSpec)
    return node.mcps[0]


def test_parse_no_tool_tags() -> None:
    spec = _parse("  bc:\n    url: https://x/mcp\n")
    assert _mcp(spec).tool_tags == ()
    assert _mcp(spec).tool_tag_transport is None


def test_parse_one_tag() -> None:
    spec = _parse(
        "  bc:\n    url: https://x/mcp\n    tool_tags: [invoices]\n"
        "    tool_tag_transport: {type: header, header_name: X-MCP-Tool-Tag}\n"
    )
    assert _mcp(spec).tool_tags == ("invoices",)
    assert _mcp(spec).tool_tag_transport == McpToolTagTransport(
        "header", header_name="X-MCP-Tool-Tag"
    )


def test_parse_multiple_tags_deduped_in_order() -> None:
    spec = _parse(
        "  bc:\n    url: https://x/mcp\n"
        "    tool_tags: [invoices, customers, invoices]\n"
        "    tool_tag_transport: {type: query_param, param_name: tag}\n"
    )
    assert _mcp(spec).tool_tags == ("invoices", "customers")


def test_empty_tags_behaves_like_no_tags() -> None:
    spec = _parse("  bc:\n    url: https://x/mcp\n    tool_tags: []\n")
    assert _mcp(spec).tool_tags == ()


def test_tags_without_transport_parse_successfully() -> None:
    # The common case: no tool_tag_transport needed (default header applies).
    spec = _parse("  bc:\n    url: https://x/mcp\n    tool_tags: [invoices, customers]\n")
    assert _mcp(spec).tool_tags == ("invoices", "customers")
    assert _mcp(spec).tool_tag_transport is None
    # The resolved transport is the default header.
    transport = effective_tool_tag_transport(_mcp(spec))
    assert transport is not None
    assert transport.type == "header"
    assert transport.header_name == DEFAULT_TOOL_TAG_HEADER


@pytest.mark.parametrize(
    "body",
    [
        # empty-string tag
        "  bc:\n    url: https://x/mcp\n    tool_tags: ['']\n"
        "    tool_tag_transport: {type: header, header_name: X}\n",
        # non-string tag
        "  bc:\n    url: https://x/mcp\n    tool_tags: [123]\n"
        "    tool_tag_transport: {type: header, header_name: X}\n",
        # unknown transport type
        "  bc:\n    url: https://x/mcp\n    tool_tags: [invoices]\n"
        "    tool_tag_transport: {type: cookie}\n",
        # header transport missing header_name
        "  bc:\n    url: https://x/mcp\n    tool_tags: [invoices]\n"
        "    tool_tag_transport: {type: header}\n",
        # tool_tags not a list
        "  bc:\n    url: https://x/mcp\n    tool_tags: invoices\n",
    ],
)
def test_invalid_tool_tags_fail_clearly(body: str) -> None:
    with pytest.raises(ParseError) as exc:
        _parse(body)
    assert "tool_tag" in str(exc.value)


def test_public_deepwiki_example_still_parses() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    spec = YAMLParser().parse(str(repo_root / "examples" / "deepwiki_mcp_agents.yml"))
    deepwiki = next(iter(collect_mcp_specs(spec.graph).values()))
    assert deepwiki.tool_tags == ()  # no tags -> unchanged


# -- engine discovery wiring -------------------------------------------------


class _FakeModel:
    """Minimal model: build() only needs bind_tools; runs are never invoked here."""

    def bind_tools(self, tools: Any) -> _FakeModel:
        return self


def _model_factory(provider: str, name: str, temperature: float | None) -> BaseChatModel:
    return cast(BaseChatModel, _FakeModel())


def _capturing_client(captured: dict[str, dict[str, Any]], tools_by_server: dict[str, list]):
    class FakeClient:
        def __init__(self, conf: dict[str, dict[str, Any]]) -> None:
            captured.update(conf)
            self._conf = conf

        async def get_tools(self) -> list:
            (server_id,) = self._conf
            return tools_by_server.get(server_id, [])

    return FakeClient


def _agent_with_mcps(*mcps: MCPSpec) -> SystemSpec:
    agent = AgentSpec(
        id="a",
        name="a",
        description="d",
        model=_MODEL,
        prompts=BasePromptSet(),
        mcps=mcps,
    )
    return SystemSpec(meta=SystemMeta(name="s"), defaults=None, graph=GraphNode(node=agent))


async def test_no_tags_passes_no_tag_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        _capturing_client(captured, {"plain": []}),
    )
    spec = _agent_with_mcps(MCPSpec(id="plain", url="https://x/mcp"))
    async with LangGraphEngine(Path("."), model_factory=_model_factory) as engine:
        await engine.build(spec)

    assert "headers" not in captured["plain"]
    assert captured["plain"]["url"] == "https://x/mcp"  # untouched


async def test_header_tags_applied_only_to_tagged_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, Any]] = {}
    tool = StructuredTool.from_function(lambda message: "ok", name="invoice_tool", description="d")
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        _capturing_client(captured, {"bc": [tool], "plain": []}),
    )
    spec = _agent_with_mcps(
        MCPSpec(
            id="bc",
            url="https://x/mcp",
            tool_tags=("invoices",),
            tool_tag_transport=McpToolTagTransport("header", header_name="X-MCP-Tool-Tag"),
        ),
        MCPSpec(id="plain", url="https://y/mcp"),
    )
    async with LangGraphEngine(Path("."), model_factory=_model_factory) as engine:
        await engine.build(spec)
        # Only the tagged server gets the tag header.
        assert captured["bc"]["headers"] == {"X-MCP-Tool-Tag": "invoices"}
        assert "headers" not in captured["plain"]
        # Only the tools discovered for the tagged server are bound.
        assert [t.name for t in engine._mcp_tools["bc"]] == ["invoice_tool"]
        assert engine._mcp_tools["plain"] == []


async def test_query_param_tags_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        _capturing_client(captured, {"bc": []}),
    )
    spec = _agent_with_mcps(
        MCPSpec(
            id="bc",
            url="https://x/mcp",
            tool_tags=("invoices", "customers"),
            tool_tag_transport=McpToolTagTransport("query_param", param_name="tag"),
        )
    )
    async with LangGraphEngine(Path("."), model_factory=_model_factory) as engine:
        await engine.build(spec)
    assert captured["bc"]["url"] == "https://x/mcp?tag=invoices%2Ccustomers"


async def test_tags_without_transport_apply_default_header_at_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        _capturing_client(captured, {"bc": []}),
    )
    # No tool_tag_transport -> the default header transport is applied at build.
    spec = _agent_with_mcps(MCPSpec(id="bc", url="https://x/mcp", tool_tags=("invoices",)))
    async with LangGraphEngine(Path("."), model_factory=_model_factory) as engine:
        await engine.build(spec)
    assert captured["bc"]["headers"] == {DEFAULT_TOOL_TAG_HEADER: "invoices"}
