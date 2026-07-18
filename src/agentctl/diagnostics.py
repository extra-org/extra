"""Offline spec diagnostics for the ``validate`` and ``inspect`` CLI commands.

Both reuse the existing parser/validator/loaders — no LLM calls, no MCP network,
no tool execution. ``validate`` does the same *pre-flight* the engine does before
serving (parse + schema + prompt files + import-root resolution + hook
import/instantiation) but stops before connecting MCP servers or compiling the
graph. ``inspect`` summarizes what the spec declares and what would be loaded.

Security: this module never prints tokens, Authorization values, or raw hook
runtime data — only safe metadata such as hook identity, auth mode, and tag
names.

Note: hooks are *trusted code*. Like the engine's build, ``validate`` imports
hook refs and instantiates class/plugin hooks to confirm they resolve; it never
calls hook *methods*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_engine.core.spec import AgentSpec, MCPSpec, OrchestratorSpec, SystemSpec
from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.langgraph.helpers import collect_mcp_specs, render_graph, walk
from agent_engine.generate.manifest import hook_plugin_refs
from agent_engine.loaders.import_roots import (
    ImportRootError,
    register_import_roots,
    resolve_import_roots,
)
from agent_engine.loaders.mcp_tags import effective_tool_tag_transport
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks.manager import HookManager

_MANIFEST = ("plugins", "plugins.toml")


def load_spec(path: str) -> tuple[SystemSpec, Path]:
    """Parse a spec file. Raises on a missing file or invalid YAML/schema."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    return YAMLParser().parse(str(source)), source.resolve().parent


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    import_roots: list[Path] = field(default_factory=list)
    agents: int = 0
    mcp_servers: int = 0
    hooks: int = 0
    tool_tags: dict[str, tuple[str, ...]] = field(default_factory=dict)


def validate_spec(path: str, *, register_roots: bool = True) -> ValidationResult:
    """Validate a spec offline. Never raises for *validation* problems — they are
    collected into ``ValidationResult.errors`` (a missing file / unparseable YAML
    is reported there too)."""
    result = ValidationResult(ok=False)
    try:
        spec, base_dir = load_spec(path)
    except Exception as exc:  # FileNotFound / ParseError (schema, tool_tags, hooks, ...)
        result.errors.append(str(exc))
        return result

    # Prompt files exist, access plugin present for protected nodes.
    result.errors.extend(str(e) for e in SystemSpecValidator().validate(spec, base_dir))

    # Plugin import roots, resolved relative to the spec file (not the CWD).
    try:
        if register_roots:
            result.import_roots = register_import_roots(base_dir, spec.plugins.import_roots)
        else:
            result.import_roots = resolve_import_roots(base_dir, spec.plugins.import_roots)
    except ImportRootError as exc:
        result.errors.append(str(exc))

    # Hooks: import refs / instantiate class & plugin/method hooks (trusted code).
    # plugins.toml is read only when plugin/method hooks are declared.
    try:
        HookManager.from_config(spec.hooks, manifest_path=base_dir.joinpath(*_MANIFEST))
    except Exception as exc:
        result.errors.append(f"hooks: {exc}")

    # Local tool / resolver plugin files exist (file check only — no import).
    result.errors.extend(_check_plugin_files(spec, base_dir))

    # MCP url sanity (the parser already validates tool_tags/transport).
    mcps = collect_mcp_specs(spec.graph)
    for server_id, mcp in mcps.items():
        if not isinstance(mcp.url, str) or not mcp.url.strip():
            result.errors.append(f'MCP server "{server_id}": url must be a non-empty string')

    result.agents = sum(1 for n in walk(spec.graph) if isinstance(n.node, AgentSpec))
    result.mcp_servers = len(mcps)
    result.hooks = len(spec.hooks.hooks)
    result.tool_tags = {sid: m.tool_tags for sid, m in mcps.items() if m.tool_tags}
    result.ok = not result.errors
    return result


def _check_plugin_files(spec: SystemSpec, base_dir: Path) -> list[str]:
    """Confirm declared local tool / resolver plugin files exist (no import)."""
    errors: list[str] = []
    tools_dir = base_dir / "plugins" / "tools"
    resolvers_dir = base_dir / "plugins" / "resolvers"
    for node in walk(spec.graph):
        agent = node.node
        if not isinstance(agent, AgentSpec):
            continue
        for tool in agent.tools:
            if not (tools_dir / f"{tool.id}.py").is_file():
                errors.append(
                    f'agent "{agent.id}": tool plugin missing: plugins/tools/{tool.id}.py'
                )
        if agent.resolvers and not (resolvers_dir / f"{agent.id}.py").is_file():
            errors.append(
                f'agent "{agent.id}": resolver plugin missing: plugins/resolvers/{agent.id}.py'
            )
    return errors


def format_validation_report(result: ValidationResult) -> str:
    if not result.ok:
        lines = ["Validation failed:"]
        lines.extend(f"  ✗ {e}" for e in result.errors)
        return "\n".join(lines)

    lines = [
        "✓ YAML parsed",
        f"✓ plugins.import_roots resolved: {len(result.import_roots)}",
        f"✓ agents: {result.agents}",
        f"✓ MCP servers: {result.mcp_servers}",
        f"✓ hooks: {result.hooks}",
    ]
    for server_id, tags in result.tool_tags.items():
        lines.append(f"✓ tool_tags: {server_id} -> {','.join(tags)}")
    lines.append("✓ validation passed")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def inspect_spec(path: str) -> str:
    """Return a human-readable, offline summary of a spec. Raises only if the
    file cannot be parsed (an invalid spec has nothing to summarize)."""
    spec, base_dir = load_spec(path)
    return _format_inspection_report(spec, base_dir, Path(path))


def _format_inspection_report(spec: SystemSpec, base_dir: Path, path: Path) -> str:
    out: list[str] = []
    warnings: list[str] = []

    out.append(f"spec: {path}")
    out.append(f"system: {spec.meta.name}")

    # Import roots
    out.append("")
    out.append("import_roots:")
    if spec.plugins.import_roots:
        for root in spec.plugins.import_roots:
            resolved = (base_dir / root).resolve()
            status = "" if resolved.is_dir() else "  (MISSING)"
            out.append(f"  - {root} -> {resolved}{status}")
            if not resolved.is_dir():
                warnings.append(f"import root not found: {root}")
    else:
        out.append("  (none)")

    # Agents
    mcps = collect_mcp_specs(spec.graph)
    agents = [n.node for n in walk(spec.graph) if isinstance(n.node, AgentSpec)]
    orchestrators = [n.node for n in walk(spec.graph) if isinstance(n.node, OrchestratorSpec)]
    out.append("")
    out.append(f"agents: {len(agents)}")
    for agent in agents:
        out.append(f"  - {agent.id} ({agent.name})")
        out.append(f"      model: {agent.model.provider}/{agent.model.name}")
        if agent.model.fallback:
            out.append(
                f"      fallback: {agent.model.fallback.provider}/{agent.model.fallback.name}"
            )
        if agent.prompts.system:
            out.append(f"      prompt: {agent.prompts.system}")
        out.append(f"      tools: {', '.join(t.id for t in agent.tools) or '(none)'}")
        out.append(f"      mcps: {', '.join(m.id for m in agent.mcps) or '(none)'}")

    # Graph
    out.append("")
    out.append(f"graph: {len(agents) + len(orchestrators)} node(s)")
    out.extend(f"  {line}" for line in render_graph(spec.graph))

    # MCP servers
    out.append("")
    out.append(f"mcp_servers: {len(mcps)}")
    for server_id, mcp in mcps.items():
        out.extend(_inspect_mcp(server_id, mcp, base_dir))
    if not mcps:
        warnings.append("no MCP servers configured")

    # Hooks
    out.append("")
    out.append(f"hooks: {len(spec.hooks.hooks)}")
    manifest = base_dir.joinpath(*_MANIFEST)
    plugin_refs = hook_plugin_refs(manifest) if manifest.is_file() else {}
    for hook in spec.hooks.hooks:
        out.extend(_inspect_hook(hook, plugin_refs))
    if not spec.hooks.hooks:
        warnings.append("no hooks configured")

    # Plugins manifest
    out.append("")
    out.append("plugins_manifest:")
    out.append(f"  path: {manifest}")
    out.append(f"  exists: {str(manifest.is_file()).lower()}")
    if plugin_refs:
        out.append(f"  hook_plugin_ids: {', '.join(sorted(plugin_refs))}")

    # tool_tags-without-live-server hint
    if any(m.tool_tags for m in mcps.values()):
        warnings.append("tool_tags configured — tag-aware discovery cannot be verified offline")

    out.append("")
    if warnings:
        out.append("warnings:")
        out.extend(f"  ! {w}" for w in warnings)
    else:
        out.append("warnings: (none)")
    return "\n".join(out)


def _inspect_mcp(server_id: str, mcp: MCPSpec, base_dir: Path) -> list[str]:
    auth_plugin = (base_dir / "plugins" / "mcp_auth" / f"{server_id}.py").is_file()
    lines = [
        f"  - {server_id}",
        f"      url: {mcp.url}",
        "      transport: streamable_http",
        f"      auth: {'plugin' if (mcp.auth or auth_plugin) else 'none'}",
    ]
    if mcp.tool_tags:
        transport = effective_tool_tag_transport(mcp)
        assert transport is not None
        default = mcp.tool_tag_transport is None
        lines.append(f"      tool_tags: {', '.join(mcp.tool_tags)}")
        detail = (
            f"header {transport.header_name}"
            if transport.type == "header"
            else f"query_param {transport.param_name}"
        )
        lines.append(f"      tool_tag_transport: {detail} ({'default' if default else 'override'})")
    else:
        lines.append("      tool_tags: (none)")
    return lines


def _inspect_hook(hook: object, plugin_refs: dict[str, str]) -> list[str]:
    point = getattr(hook, "point", "?")
    ref = getattr(hook, "ref", None)
    plugin = getattr(hook, "plugin", None)
    method = getattr(hook, "method", None)
    failure_policy = getattr(hook, "failure_policy", "fail")

    if ref:
        target = f"ref={ref}"
    else:
        resolved = "yes" if plugin in plugin_refs else "no"
        target = f"plugin={plugin} method={method} (resolved_from_manifest={resolved})"

    return [f"  - {point}: {target}", f"      failure_policy: {failure_policy}"]
