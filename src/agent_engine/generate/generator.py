from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_engine.core.spec import AgentSpec, GraphNode, SystemSpec
from agent_engine.generate.manifest import (
    ensure_plugins_manifest_exists,
    manifest_package,
    update_manifest,
)


@dataclass
class GenerateResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class Generator:
    """Generates plugin stubs for tools, resolvers, and prompt files declared in the spec.

    Never overwrites existing files — only creates missing stubs.

    Shared resolvers (scope: shared) are generated once in plugins/resolvers/shared.py
    as class SharedResolver. Per-agent files inherit from SharedResolver and only
    include stubs for their agent-scoped resolvers.
    """

    def generate(self, spec: SystemSpec, base_dir: Path) -> GenerateResult:
        result = GenerateResult()
        (
            tool_ids,
            shared_ids,
            agent_resolver_ids,
            agents_with_shared,
            has_protected,
            mcp_plugin_ids,
        ) = _collect(spec.graph)

        tools_dir = base_dir / "plugins" / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        for tool_id, description in tool_ids.items():
            self._write(
                tools_dir / f"{tool_id}.py", _tool_stub(tool_id, description), result, base_dir
            )

        resolvers_dir = base_dir / "plugins" / "resolvers"
        resolvers_dir.mkdir(parents=True, exist_ok=True)

        if shared_ids:
            self._write(
                resolvers_dir / "shared.py",
                _shared_resolver_stub(shared_ids),
                result,
                base_dir,
            )

        for agent_id, agent_only_ids in agent_resolver_ids.items():
            has_shared = agent_id in agents_with_shared
            self._write(
                resolvers_dir / f"{agent_id}.py",
                _agent_resolver_stub(agent_only_ids, has_shared),
                result,
                base_dir,
            )

        if has_protected:
            self._write(
                base_dir / "plugins" / "access.py",
                _access_stub(),
                result,
                base_dir,
            )

        if mcp_plugin_ids:
            mcp_auth_dir = base_dir / "plugins" / "mcp_auth"
            mcp_auth_dir.mkdir(parents=True, exist_ok=True)
            for mcp_id in mcp_plugin_ids:
                self._write(mcp_auth_dir / f"{mcp_id}.py", _mcp_auth_stub(mcp_id), result, base_dir)

        hook_methods = _collect_hook_methods(spec)
        if hook_methods:
            hooks_dir = base_dir / "plugins" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            init = hooks_dir / "__init__.py"
            if not init.exists():
                init.write_text('"""Generated runtime hook plugins."""\n', encoding="utf-8")
            for plugin_id, methods in hook_methods.items():
                self._write(
                    hooks_dir / f"{plugin_id}.py",
                    _hook_plugin_stub(plugin_id, methods),
                    result,
                    base_dir,
                )
        prompt_paths = _collect_prompts(spec.graph)
        for path_str in prompt_paths:
            path = base_dir / path_str
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write(path, "<!-- STUB: fill in this prompt -->\n", result, base_dir)

        self._sync_manifest(
            base_dir / "plugins",
            spec,
            tool_ids,
            shared_ids,
            agent_resolver_ids,
            hook_methods,
            result,
        )
        return result

    def _sync_manifest(
        self,
        plugins_root: Path,
        spec: SystemSpec,
        tool_ids: dict[str, str],
        shared_ids: list[str],
        agent_resolver_ids: dict[str, list[str]],
        hook_methods: dict[str, list[str]],
        result: GenerateResult,
    ) -> None:
        """Create/update the single plugins.toml manifest for this package.

        The manifest records importable refs for generated resolvers/tools and
        hook plugin classes. The runtime reads only [hooks.plugins] to resolve
        managed hook ids; other sections are documentation/generation metadata.
        """
        manifest_path, created = ensure_plugins_manifest_exists(plugins_root)
        pkg = manifest_package(manifest_path)

        resolver_refs: dict[str, str] = {}
        if shared_ids:
            resolver_refs["shared"] = f"{pkg}.resolvers.shared:SharedResolver"
        for agent_id in agent_resolver_ids:
            resolver_refs[agent_id] = f"{pkg}.resolvers.{agent_id}:Resolver"

        tool_refs = {tool_id: f"{pkg}.tools.{tool_id}:{tool_id}" for tool_id in tool_ids}

        hook_refs: dict[str, list[str]] = {}
        hook_plugins: dict[str, str] = {}
        for hook in spec.hooks.hooks:
            if hook.ref:
                hook_refs.setdefault(hook.point, []).append(hook.ref)
        for plugin_id in hook_methods:
            hook_plugins[plugin_id] = f"{pkg}.hooks.{plugin_id}:{_hook_class_name(plugin_id)}"

        update_manifest(
            manifest_path,
            hooks=hook_refs,
            hook_plugins=hook_plugins,
            resolvers=resolver_refs,
            tools=tool_refs,
        )
        base_dir = plugins_root.parent
        rel_manifest = manifest_path.relative_to(base_dir)
        (result.created if created else result.skipped).append(str(rel_manifest))

    def _write(self, path: Path, content: str, result: GenerateResult, base_dir: Path) -> None:
        rel_path = path.relative_to(base_dir)
        if path.exists():
            result.skipped.append(str(rel_path))
            return
        path.write_text(content, encoding="utf-8")
        result.created.append(str(rel_path))


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
        tool_ids,
        shared_ids,
        agent_resolver_ids,
        agents_with_shared,
        has_protected,
        mcp_plugin_ids,
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


def _collect_hook_methods(spec: SystemSpec) -> dict[str, list[str]]:
    methods: dict[str, list[str]] = {}
    for hook in spec.hooks.hooks:
        if not hook.plugin or not hook.method:
            continue
        plugin_methods = methods.setdefault(hook.plugin, [])
        if hook.method not in plugin_methods:
            plugin_methods.append(hook.method)
    return dict(sorted(methods.items()))


def _hook_class_name(plugin_id: str) -> str:
    return "".join(part.capitalize() for part in plugin_id.split("_") if part) + "Hook"


def _hook_plugin_stub(plugin_id: str, methods: list[str]) -> str:
    class_name = _hook_class_name(plugin_id)
    method_blocks = "\n".join(
        f"    async def {method}(self, event: HookInvocation) -> object:\n"
        "        raise NotImplementedError\n"
        for method in methods
    )
    return (
        "from __future__ import annotations\n\n"
        "from agent_engine.runtime.hooks import HookInvocation\n\n\n"
        f"class {class_name}:\n"
        "    def __init__(self) -> None:\n"
        "        # Safe long-lived state can live here: initialized clients,\n"
        "        # tenant metadata, keyed caches, audit/metrics clients.\n"
        "        self._cache: dict[str, object] = {}\n"
        "        # Do not store per-request state such as current user, current\n"
        "        # organization, inbound tokens, request objects, or last headers.\n"
        "        # Read that data from event.run_context and event.payload.\n\n"
        f"{method_blocks}"
    )


def _collect_prompts(node: GraphNode) -> list[str]:
    """Walk the graph and collect all declared prompt paths (system, user, orchestrator).

    Deduplicates paths so the same file declared by multiple nodes is only
    scaffolded once — consistent with how _collect() deduplicates tool/resolver IDs.
    """
    paths: list[str] = []
    seen: set[str] = set()

    def walk(n: GraphNode) -> None:
        prompts = n.node.get_prompts()
        for field_name in ("system", "user", "orchestrator"):
            path_str = getattr(prompts, field_name, None)
            if path_str and path_str not in seen:
                seen.add(path_str)
                paths.append(path_str)
        for child in n.children:
            walk(child)

    walk(node)
    return paths
