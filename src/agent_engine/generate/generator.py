from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_engine.core.spec import AgentSpec, GraphNode, OrchestratorSpec, SystemSpec


@dataclass
class GenerateResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class Generator:
    """Generates plugin stubs for tools and resolvers declared in the spec.

    Never overwrites existing files — only creates missing stubs.
    """

    def generate(self, spec: SystemSpec, base_dir: Path) -> GenerateResult:
        result = GenerateResult()
        tool_ids, resolver_agent_ids, has_protected = _collect(spec.graph)

        tools_dir = base_dir / "plugins" / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        for tool_id, description in tool_ids.items():
            self._write(tools_dir / f"{tool_id}.py", _tool_stub(tool_id, description), result)

        resolvers_dir = base_dir / "plugins" / "resolvers"
        resolvers_dir.mkdir(parents=True, exist_ok=True)
        for agent_id, resolver_ids in resolver_agent_ids.items():
            self._write(
                resolvers_dir / f"{agent_id}.py",
                _resolver_stub(agent_id, resolver_ids),
                result,
            )

        if has_protected:
            self._write(
                base_dir / "plugins" / "access.py",
                _access_stub(),
                result,
            )

        return result

    def _write(self, path: Path, content: str, result: GenerateResult) -> None:
        rel = str(path.relative_to(path.parents[3]) if path.parents[3].name == "plugins" else path)
        if path.exists():
            result.skipped.append(str(path.name))
            return
        path.write_text(content, encoding="utf-8")
        result.created.append(str(path.name))


def _collect(
    node: GraphNode,
) -> tuple[dict[str, str], dict[str, list[str]], bool]:
    tool_ids: dict[str, str] = {}
    resolver_agent_ids: dict[str, list[str]] = {}
    has_protected = False

    def walk(n: GraphNode) -> None:
        nonlocal has_protected
        if n.node.protected:
            has_protected = True
        if isinstance(n.node, AgentSpec):
            for t in n.node.tools:
                tool_ids.setdefault(t.id, t.description)
            if n.node.resolvers:
                resolver_agent_ids.setdefault(n.node.id, []).extend(
                    r.id for r in n.node.resolvers
                    if r.id not in resolver_agent_ids.get(n.node.id, [])
                )
        for child in n.children:
            walk(child)

    walk(node)
    return tool_ids, resolver_agent_ids, has_protected


def _tool_stub(tool_id: str, description: str) -> str:
    return (
        f"def {tool_id}(input: dict) -> str:\n"
        f'    """{description}"""\n'
        f"    raise NotImplementedError\n"
    )


def _resolver_stub(agent_id: str, resolver_ids: list[str]) -> str:
    methods = "\n".join(
        f"    def {r_id}(self, ctx: dict) -> str:\n"
        f'        """Returns the value for {{{{{r_id}}}}}"""\n'
        f"        raise NotImplementedError\n"
        for r_id in dict.fromkeys(resolver_ids)
    )
    return (
        f"class Resolver:\n"
        f"    def __init__(self) -> None:\n"
        f"        pass\n\n"
        f"{methods}"
    )


def _access_stub() -> str:
    return (
        "class AccessResolver:\n"
        "    def can_access(self, ctx: dict, node_id: str) -> bool:\n"
        "        raise NotImplementedError\n"
    )
