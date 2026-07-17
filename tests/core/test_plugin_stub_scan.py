"""Tests for the startup scan of generated-but-unimplemented plugin stubs.

The scan is static (AST) — plugin files are parsed, never executed — so these
tests write real plugin files into tmp_path and assert on the errors returned.
"""

from __future__ import annotations

from pathlib import Path

from agent_engine.core.plugin_stubs import scan_unimplemented_plugins
from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    MCPSpec,
    ModelConfig,
    OrchestratorPromptSet,
    OrchestratorSpec,
    ResolverSpec,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.core.validator import SystemSpecValidator

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


def agent(
    node_id: str,
    *,
    tools: tuple[ToolSpec, ...] = (),
    resolvers: tuple[ResolverSpec, ...] = (),
    mcps: tuple[MCPSpec, ...] = (),
    protected: bool = False,
) -> GraphNode:
    spec = AgentSpec(
        id=node_id,
        name=node_id,
        description=f"{node_id} agent",
        model=_MODEL,
        protected=protected,
        prompts=BasePromptSet(),
        tools=tools,
        resolvers=resolvers,
        mcps=mcps,
    )
    return GraphNode(node=spec)


def orchestrator(node_id: str, children: list[GraphNode]) -> GraphNode:
    spec = OrchestratorSpec(
        id=node_id,
        name=node_id,
        description=f"{node_id} orchestrator",
        model=_MODEL,
        prompts=OrchestratorPromptSet(),
    )
    return GraphNode(node=spec, children=tuple(children))


def system(graph: GraphNode) -> SystemSpec:
    return SystemSpec(meta=SystemMeta(name="test-system"), defaults=None, graph=graph)


def write(base: Path, rel: str, text: str) -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


STUB_TOOL = (
    "def {name}(input: dict) -> str:\n"
    '    """Does a thing."""\n'
    "    raise NotImplementedError\n"
)
REAL_TOOL = "def {name}(input: dict) -> str:\n    return 'done'\n"


def scan(spec: SystemSpec, base: Path) -> list[str]:
    return [f"{e.field}: {e.message}" for e in scan_unimplemented_plugins(spec, base)]


# -- tools --------------------------------------------------------------------


def test_stub_tool_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "plugins/tools/lookup.py", STUB_TOOL.format(name="lookup"))
    spec = system(agent("a", tools=(ToolSpec("lookup", "looks up"),)))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "not implemented" in errors[0]
    assert "lookup" in errors[0]


def test_implemented_tool_passes(tmp_path: Path) -> None:
    write(tmp_path, "plugins/tools/lookup.py", REAL_TOOL.format(name="lookup"))
    spec = system(agent("a", tools=(ToolSpec("lookup", "looks up"),)))

    assert scan(spec, tmp_path) == []


def test_missing_tool_file_is_reported(tmp_path: Path) -> None:
    spec = system(agent("a", tools=(ToolSpec("lookup", "looks up"),)))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "not found" in errors[0]


def test_shared_tool_reported_once(tmp_path: Path) -> None:
    write(tmp_path, "plugins/tools/lookup.py", STUB_TOOL.format(name="lookup"))
    spec = system(
        orchestrator(
            "root",
            [
                agent("a", tools=(ToolSpec("lookup", "looks up"),)),
                agent("b", tools=(ToolSpec("lookup", "looks up"),)),
            ],
        )
    )

    assert len(scan(spec, tmp_path)) == 1


# -- resolvers ------------------------------------------------------------------


def test_stub_resolver_is_reported(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/resolvers/a.py",
        "class Resolver:\n"
        "    def user_name(self, ctx: dict) -> str:\n"
        "        raise NotImplementedError\n",
    )
    spec = system(agent("a", resolvers=(ResolverSpec(id="user_name", scope="agent"),)))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "user_name" in errors[0]


def test_resolver_implemented_in_shared_base_passes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/resolvers/shared.py",
        "class SharedResolver:\n"
        "    def user_name(self, ctx: dict) -> str:\n"
        "        return 'guy'\n",
    )
    write(
        tmp_path,
        "plugins/resolvers/a.py",
        "from shared import SharedResolver\n\n"
        "class Resolver(SharedResolver):\n"
        "    pass\n",
    )
    spec = system(agent("a", resolvers=(ResolverSpec(id="user_name", scope="shared"),)))

    assert scan(spec, tmp_path) == []


def test_stub_resolver_in_shared_base_is_reported(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/resolvers/shared.py",
        "class SharedResolver:\n"
        "    def user_name(self, ctx: dict) -> str:\n"
        "        raise NotImplementedError\n",
    )
    write(
        tmp_path,
        "plugins/resolvers/a.py",
        "from shared import SharedResolver\n\n"
        "class Resolver(SharedResolver):\n"
        "    pass\n",
    )
    spec = system(agent("a", resolvers=(ResolverSpec(id="user_name", scope="shared"),)))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "user_name" in errors[0]


def test_missing_resolver_file_is_reported(tmp_path: Path) -> None:
    spec = system(agent("a", resolvers=(ResolverSpec(id="user_name", scope="agent"),)))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "not found" in errors[0]


def test_unknown_method_is_skipped_not_flagged(tmp_path: Path) -> None:
    # Method may come from a base class the scan can't see — stay silent.
    write(
        tmp_path,
        "plugins/resolvers/a.py",
        "from mylib import CustomBase\n\n"
        "class Resolver(CustomBase):\n"
        "    pass\n",
    )
    spec = system(agent("a", resolvers=(ResolverSpec(id="user_name", scope="agent"),)))

    assert scan(spec, tmp_path) == []


# -- access ---------------------------------------------------------------------


def test_stub_access_resolver_is_reported_for_protected_nodes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/access.py",
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        "        raise NotImplementedError\n",
    )
    spec = system(orchestrator("root", [agent("admin", protected=True)]))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "can_access" in errors[0]


def test_implemented_access_resolver_passes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/access.py",
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        "        return True\n",
    )
    spec = system(orchestrator("root", [agent("admin", protected=True)]))

    assert scan(spec, tmp_path) == []


def test_stub_access_resolver_ignored_without_protected_nodes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/access.py",
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        "        raise NotImplementedError\n",
    )
    spec = system(agent("a"))

    assert scan(spec, tmp_path) == []


# -- MCP auth ---------------------------------------------------------------------


def test_stub_mcp_auth_is_reported(tmp_path: Path) -> None:
    write(
        tmp_path,
        "plugins/mcp_auth/orders.py",
        "async def get_headers() -> dict[str, str]:\n"
        '    """Auth headers."""\n'
        "    raise NotImplementedError\n",
    )
    mcp = MCPSpec(id="orders", url="https://mcp.example.com/mcp", auth=True)
    spec = system(agent("a", mcps=(mcp,)))

    errors = scan(spec, tmp_path)

    assert len(errors) == 1
    assert "orders" in errors[0]


def test_mcp_without_auth_not_scanned(tmp_path: Path) -> None:
    mcp = MCPSpec(id="orders", url="https://mcp.example.com/mcp", auth=False)
    spec = system(agent("a", mcps=(mcp,)))

    assert scan(spec, tmp_path) == []


# -- wiring through SystemSpecValidator -------------------------------------------


def test_validator_includes_stub_errors(tmp_path: Path) -> None:
    write(tmp_path, "plugins/tools/lookup.py", STUB_TOOL.format(name="lookup"))
    spec = system(agent("a", tools=(ToolSpec("lookup", "looks up"),)))

    errors = SystemSpecValidator().validate(spec, tmp_path)

    assert any("not implemented" in e.message for e in errors)


# -- prompts --------------------------------------------------------------------


def test_stub_prompts_are_reported(tmp_path: Path) -> None:
    # 1. Test Agent stub prompts
    write(tmp_path, "prompts/a/system.md", "<!-- STUB: fill in this prompt -->")
    write(tmp_path, "prompts/a/user.md", "some real user prompt")

    agent_node = GraphNode(
        node=AgentSpec(
            id="a",
            name="a",
            description="a agent",
            model=_MODEL,
            prompts=BasePromptSet(system="prompts/a/system.md", user="prompts/a/user.md"),
        )
    )

    # 2. Test Orchestrator stub prompts
    write(tmp_path, "prompts/orch.md", "<!-- STUB: fill in this prompt -->")
    orch_node = GraphNode(
        node=OrchestratorSpec(
            id="orch",
            name="orch",
            description="orch orchestrator",
            model=_MODEL,
            prompts=OrchestratorPromptSet(orchestrator="prompts/orch.md"),
        ),
        children=(agent_node,),
    )

    spec = system(orch_node)
    errors = scan(spec, tmp_path)

    assert len(errors) == 2
    assert any("a.prompts.system" in e and "is declared but not implemented" in e for e in errors)
    assert any(
        "orch.prompts.orchestrator" in e and "is declared but not implemented" in e for e in errors
    )


def test_implemented_prompts_pass(tmp_path: Path) -> None:
    write(tmp_path, "prompts/a/system.md", "some real system prompt")
    write(tmp_path, "prompts/a/user.md", "some real user prompt")

    agent_node = GraphNode(
        node=AgentSpec(
            id="a",
            name="a",
            description="a agent",
            model=_MODEL,
            prompts=BasePromptSet(system="prompts/a/system.md", user="prompts/a/user.md"),
        )
    )

    spec = system(agent_node)
    assert scan(spec, tmp_path) == []


def test_duplicate_prompt_path_reported_once(tmp_path: Path) -> None:
    """The same prompt file path declared by two nodes must only generate
    one error — consistent with how shared tools are reported once."""
    write(tmp_path, "prompts/shared.md", "<!-- STUB: fill in this prompt -->")
    shared_prompt = BasePromptSet(system="prompts/shared.md")

    spec = system(
        GraphNode(
            node=OrchestratorSpec(
                id="root",
                name="root",
                description="root",
                model=_MODEL,
                prompts=OrchestratorPromptSet(),
            ),
            children=(
                GraphNode(
                    node=AgentSpec(
                        id="a",
                        name="a",
                        description="a",
                        model=_MODEL,
                        prompts=shared_prompt,
                    )
                ),
                GraphNode(
                    node=AgentSpec(
                        id="b",
                        name="b",
                        description="b",
                        model=_MODEL,
                        prompts=shared_prompt,
                    )
                ),
            ),
        )
    )
    errors = scan(spec, tmp_path)
    assert len(errors) == 1


def test_stub_user_prompt_is_reported(tmp_path: Path) -> None:
    """Error for an unimplemented user prompt must be correctly reported."""
    write(tmp_path, "prompts/a/user.md", "<!-- STUB: fill in this prompt -->")
    spec = system(
        GraphNode(
            node=AgentSpec(
                id="a",
                name="a",
                description="a agent",
                model=_MODEL,
                prompts=BasePromptSet(user="prompts/a/user.md"),
            )
        )
    )
    errors = scan(spec, tmp_path)
    assert len(errors) == 1
    assert any("a.prompts.user" in e and "is declared but not implemented" in e for e in errors)


def test_missing_prompt_file_is_reported_with_hint(tmp_path: Path) -> None:
    """Missing prompt file must be reported with the run generate hint."""
    spec = system(
        GraphNode(
            node=AgentSpec(
                id="a",
                name="a",
                description="a agent",
                model=_MODEL,
                prompts=BasePromptSet(system="prompts/a/system.md"),
            )
        )
    )
    errors = scan(spec, tmp_path)
    assert len(errors) == 1
    assert any(
        "a.prompts.system" in e and "Prompt file not found: prompts/a/system.md — run `agentctl generate` to create the stub" in e
        for e in errors
    )


