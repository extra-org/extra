from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_engine.core.spec import AgentSpec, GraphNode, SystemSpec


@dataclass
class GenerateResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class Generator:
    """Generates plugin stubs for tools and resolvers declared in the spec.

    Never overwrites existing files — only creates missing stubs.

    Shared resolvers (scope: shared) are generated once in plugins/resolvers/shared.py
    as class SharedResolver. Per-agent files inherit from SharedResolver and only
    include stubs for their agent-scoped resolvers.
    """

    def generate(self, spec: SystemSpec, base_dir: Path) -> GenerateResult:
        result = GenerateResult()
        (
            tool_ids, shared_ids, agent_resolver_ids, agents_with_shared, has_protected,
            mcp_plugin_ids,
        ) = _collect(spec.graph)

        tools_dir = base_dir / "plugins" / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        for tool_id, description in tool_ids.items():
            self._write(tools_dir / f"{tool_id}.py", _tool_stub(tool_id, description), result)

        resolvers_dir = base_dir / "plugins" / "resolvers"
        resolvers_dir.mkdir(parents=True, exist_ok=True)

        if shared_ids:
            self._write(
                resolvers_dir / "shared.py",
                _shared_resolver_stub(shared_ids),
                result,
            )

        for agent_id, agent_only_ids in agent_resolver_ids.items():
            has_shared = agent_id in agents_with_shared
            self._write(
                resolvers_dir / f"{agent_id}.py",
                _agent_resolver_stub(agent_only_ids, has_shared),
                result,
            )

        if has_protected:
            self._write(
                base_dir / "plugins" / "access.py",
                _access_stub(),
                result,
            )

        if mcp_plugin_ids:
            mcp_auth_dir = base_dir / "plugins" / "mcp_auth"
            mcp_auth_dir.mkdir(parents=True, exist_ok=True)
            for mcp_id in mcp_plugin_ids:
                self._write(mcp_auth_dir / f"{mcp_id}.py", _mcp_auth_stub(mcp_id), result)

        return result

    def _write(self, path: Path, content: str, result: GenerateResult) -> None:
        if path.exists():
            result.skipped.append(str(path.name))
            return
        path.write_text(content, encoding="utf-8")
        result.created.append(str(path.name))


def _collect(
    node: GraphNode,
) -> tuple[dict[str, str], list[str], dict[str, list[str]], set[str], bool, list[str]]:
    """Walk the graph and collect:
    - tool_ids: {id: description}
    - shared_ids: ordered list of shared resolver IDs (deduped across all agents)
    - agent_resolver_ids: {agent_id: [agent-scoped resolver ids for that agent]}
    - agents_with_shared: set of agent IDs that reference at least one shared resolver
    - has_protected: whether any node is protected
    """
    tool_ids: dict[str, str] = {}
    shared_ids: list[str] = []
    seen_shared: set[str] = set()
    agent_resolver_ids: dict[str, list[str]] = {}
    agents_with_shared: set[str] = set()
    has_protected = False
    mcp_plugin_ids: list[str] = []
    seen_mcp_plugins: set[str] = set()

    def walk(n: GraphNode) -> None:
        nonlocal has_protected
        if n.node.protected:
            has_protected = True
        if isinstance(n.node, AgentSpec):
            for t in n.node.tools:
                tool_ids.setdefault(t.id, t.description)
            for r in n.node.resolvers:
                if r.scope == "shared":
                    if r.id not in seen_shared:
                        shared_ids.append(r.id)
                        seen_shared.add(r.id)
                    agents_with_shared.add(n.node.id)
                else:
                    ids = agent_resolver_ids.setdefault(n.node.id, [])
                    if r.id not in ids:
                        ids.append(r.id)
            for mcp in n.node.mcps:
                if mcp.auth and mcp.id not in seen_mcp_plugins:
                    mcp_plugin_ids.append(mcp.id)
                    seen_mcp_plugins.add(mcp.id)
        for child in n.children:
            walk(child)

    walk(node)

    # Agents that only have shared resolvers still need a stub file.
    for agent_id in agents_with_shared:
        agent_resolver_ids.setdefault(agent_id, [])

    return (
        tool_ids, shared_ids, agent_resolver_ids, agents_with_shared, has_protected, mcp_plugin_ids
    )


def _tool_stub(tool_id: str, description: str) -> str:
    return (
        f"def {tool_id}(input: dict) -> str:\n"
        f'    """{description}"""\n'
        f"    raise NotImplementedError\n"
    )


def _shared_resolver_stub(resolver_ids: list[str]) -> str:
    methods = "\n".join(
        f"    def {r_id}(self, ctx: dict) -> str:\n"
        f'        """Returns the value for {{{{{r_id}}}}}"""\n'
        f"        raise NotImplementedError\n"
        for r_id in resolver_ids
    )
    return (
        "from __future__ import annotations\n\n\n"
        "class SharedResolver:\n"
        "    def __init__(self) -> None:\n"
        "        pass\n\n"
        f"{methods}"
    )


def _agent_resolver_stub(agent_only_ids: list[str], inherits_shared: bool) -> str:
    if inherits_shared:
        header = (
            "from __future__ import annotations\n\n"
            "from shared import SharedResolver\n\n\n"
            "class Resolver(SharedResolver):\n"
        )
        init = "    def __init__(self) -> None:\n        super().__init__()\n"
    else:
        header = "from __future__ import annotations\n\n\nclass Resolver:\n"
        init = "    def __init__(self) -> None:\n        pass\n"

    if agent_only_ids:
        methods = "\n" + "\n".join(
            f"    def {r_id}(self, ctx: dict) -> str:\n"
            f'        """Returns the value for {{{{{r_id}}}}}"""\n'
            f"        raise NotImplementedError\n"
            for r_id in agent_only_ids
        )
    else:
        methods = ""

    return f"{header}{init}{methods}"


def _access_stub() -> str:
    return (
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        "        raise NotImplementedError\n"
    )


def _mcp_auth_stub(mcp_id: str) -> str:
    return (
        "from __future__ import annotations\n\n\n"
        "async def get_headers() -> dict[str, str]:\n"
        f'    """Return HTTP headers to authenticate requests to the {mcp_id} MCP server.\n'
        "\n"
        "    Called on every request, so cache the token and only refresh it near\n"
        "    expiry (e.g. for OAuth or other short-lived credentials).\n"
        '    """\n'
        "    raise NotImplementedError\n"
    )
