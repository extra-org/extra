from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from agent_engine.core.errors import ValidationError
from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    DefaultsConfig,
    GraphNode,
    HooksConfig,
    HookSpec,
    MCPSpec,
    McpToolTagTransport,
    ModelConfig,
    OrchestratorPromptSet,
    OrchestratorSpec,
    PluginsConfig,
    ResolverSpec,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.parser import Parser
from agent_engine.runtime.hooks.models import HOOK_POINTS

_SECRET_MARKERS = ("api_key", "apikey", "secret", "token", "password", "private_key")


def _validate_plugins(plugins: Any, errors: list[ValidationError]) -> None:
    if plugins is None:
        return
    if not isinstance(plugins, dict):
        errors.append(ValidationError("plugins", "Must be a mapping"))
        return
    roots = plugins.get("import_roots")
    if roots is None:
        return
    if not isinstance(roots, list):
        errors.append(
            ValidationError("plugins.import_roots", "Must be a list of directory paths")
        )
        return
    for i, root in enumerate(roots):
        if not isinstance(root, str):
            errors.append(
                ValidationError(f"plugins.import_roots[{i}]", "Must be a string path")
            )


def _validate_node_refs(
        orchestrators: dict[str, Any],
    agents: dict[str, Any],
    resolvers: dict[str, Any],
    tools: dict[str, Any],
    mcps: dict[str, Any],
    errors: list[ValidationError],
) -> None:
    for node_id, spec in orchestrators.items():
        if not isinstance(spec, dict):
            continue
        if not spec.get("prompts", {}).get("orchestrator"):
            errors.append(
                ValidationError(
                    f"orchestrators.{node_id}.prompts.orchestrator",
                    "Required for orchestrators",
                )
            )
        for ref in spec.get("resolvers", []):
            if ref not in resolvers:
                errors.append(
                    ValidationError(
                        f"orchestrators.{node_id}.resolvers",
                        f"Unknown resolver '{ref}'",
                    )
                )

    for node_id, spec in agents.items():
        if not isinstance(spec, dict):
            continue
        for ref in spec.get("resolvers", []):
            if ref not in resolvers:
                errors.append(
                    ValidationError(f"agents.{node_id}.resolvers", f"Unknown resolver '{ref}'")
                )
        for ref in spec.get("tools", []):
            if ref not in tools:
                errors.append(
                    ValidationError(f"agents.{node_id}.tools", f"Unknown tool '{ref}'")
                )
        for ref in spec.get("mcps", []):
            if ref not in mcps:
                errors.append(ValidationError(f"agents.{node_id}.mcps", f"Unknown MCP '{ref}'"))


def _build_mcp_spec(ref: str, raw: dict[str, Any]) -> MCPSpec:
    tool_tags = _dedupe_stable(
        str(t) for t in (raw.get("tool_tags") or []) if isinstance(t, str)
    )
    transport_raw = raw.get("tool_tag_transport")
    transport = None
    if isinstance(transport_raw, dict):
        transport = McpToolTagTransport(
            type=str(transport_raw.get("type", "")),
            header_name=transport_raw.get("header_name"),
            param_name=transport_raw.get("param_name"),
        )
    return MCPSpec(
        id=ref,
        url=raw["url"],
        auth=bool(raw.get("auth", False)),
        tool_tags=tool_tags,
        tool_tag_transport=transport,
    )


def _build_hooks(raw: Any) -> HooksConfig:
    if not isinstance(raw, dict):
        return HooksConfig()
    specs: list[HookSpec] = []
    for point, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            specs.append(
                HookSpec(
                    point=point,
                    ref=entry.get("ref"),
                    config=dict(entry.get("config", {}) or {}),
                    failure_policy=entry.get("failure_policy", "fail"),
                    plugin=entry.get("plugin"),
                    method=entry.get("method"),
                )
            )
    return HooksConfig(hooks=tuple(specs))


def _build_plugins(raw: Any) -> PluginsConfig:
    if not isinstance(raw, dict):
        return PluginsConfig()
    roots = raw.get("import_roots")
    if not isinstance(roots, list):
        return PluginsConfig()
    return PluginsConfig(import_roots=tuple(r for r in roots if isinstance(r, str)))


def _validate_hook_entry(
        point: str, index: int, entry: Any, errors: list[ValidationError]
) -> None:
    base = f"hooks.{point}[{index}]"
    if not isinstance(entry, dict):
        errors.append(
            ValidationError(base, "Must be a mapping with either 'ref' or 'plugin' + 'method'")
        )
        return
    ref = entry.get("ref")
    plugin = entry.get("plugin")
    method = entry.get("method")
    has_ref = ref is not None
    has_plugin_or_method = plugin is not None or method is not None

    if has_ref and has_plugin_or_method:
        errors.append(
            ValidationError(
                base,
                "Use either 'ref' or 'plugin' + 'method', not both",
            )
        )
    elif has_ref:
        if not isinstance(ref, str):
            errors.append(ValidationError(f"{base}.ref", "Must be a string import path"))
    elif has_plugin_or_method:
        if plugin is None:
            errors.append(ValidationError(f"{base}.plugin", "Required when 'method' is used"))
        elif not isinstance(plugin, str):
            errors.append(ValidationError(f"{base}.plugin", "Must be a string plugin id"))
        if method is None:
            errors.append(ValidationError(f"{base}.method", "Required when 'plugin' is used"))
        elif not isinstance(method, str):
            errors.append(ValidationError(f"{base}.method", "Must be a string method name"))
    else:
        errors.append(
            ValidationError(f"{base}.ref", "Required field 'ref' or 'plugin' + 'method'")
        )

    if "config" in entry and not isinstance(entry["config"], dict):
        errors.append(ValidationError(f"{base}.config", "Must be a mapping if present"))
    policy = entry.get("failure_policy", "fail")
    if policy not in ("fail", "warn"):
        errors.append(ValidationError(f"{base}.failure_policy", "Must be 'fail' or 'warn'"))


def _validate_hooks(hooks: Any, errors: list[ValidationError]) -> None:
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        errors.append(ValidationError("hooks", "Must be a mapping of hook point -> list"))
        return
    for point, entries in hooks.items():
        if point not in HOOK_POINTS:
            errors.append(
                ValidationError(
                    f"hooks.{point}",
                    f"Unknown hook point. Valid points: {', '.join(HOOK_POINTS)}",
                )
            )
            continue
        if not isinstance(entries, list):
            errors.append(ValidationError(f"hooks.{point}", "Must be a list of hook entries"))
            continue
        for i, entry in enumerate(entries):
            _validate_hook_entry(point, i, entry, errors)


def _validate_mcp_tool_tags(
        mcp_id: str, raw: dict[str, Any], errors: list[ValidationError]
) -> None:
    base = f"mcps.{mcp_id}"
    tags = raw.get("tool_tags")
    if tags is not None and not isinstance(tags, list):
        errors.append(ValidationError(f"{base}.tool_tags", "Must be a list of strings"))
        return
    has_tags = False
    for i, tag in enumerate(tags or []):
        if not isinstance(tag, str):
            errors.append(ValidationError(f"{base}.tool_tags[{i}]", "Must be a string"))
        elif not tag.strip():
            errors.append(ValidationError(f"{base}.tool_tags[{i}]", "Must not be empty/blank"))
        else:
            has_tags = True

    # tool_tag_transport is an optional advanced override. When tags are set
    # but no transport is given, a default header transport is applied at
    # discovery time (see loaders/mcp_tags.py). Only validate it if present.
    transport = raw.get("tool_tag_transport")
    if transport is None or not has_tags:
        return
    if not isinstance(transport, dict):
        errors.append(
            ValidationError(
                f"{base}.tool_tag_transport",
                "Must be a mapping with type 'header' (header_name) or "
                "'query_param' (param_name)",
            )
        )
        return
    ttype = transport.get("type")
    if ttype == "header":
        if not isinstance(transport.get("header_name"), str) or not transport["header_name"]:
            errors.append(
                ValidationError(
                    f"{base}.tool_tag_transport.header_name", "Required for type 'header'"
                )
            )
    elif ttype == "query_param":
        if not isinstance(transport.get("param_name"), str) or not transport["param_name"]:
            errors.append(
                ValidationError(
                    f"{base}.tool_tag_transport.param_name",
                    "Required for type 'query_param'",
                )
            )
    else:
        errors.append(
            ValidationError(
                f"{base}.tool_tag_transport.type", "Must be 'header' or 'query_param'"
            )
        )


def _validate_mcps(mcps: dict[str, Any], errors: list[ValidationError]) -> None:
    for mcp_id, raw in mcps.items():
        if not isinstance(raw, dict):
            continue
        _validate_mcp_tool_tags(mcp_id, raw, errors)


def _load(path: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise ParseError([ValidationError("path", f"File not found: {path}")])
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseError([ValidationError("path", f"Cannot read file: {exc}")]) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ParseError([ValidationError("yaml", f"Invalid YAML: {exc}")]) from exc
    if not isinstance(data, dict):
        raise ParseError([ValidationError("yaml", "Root must be a mapping")])
    return data


class YAMLParser(Parser):
    def parse(self, path: str) -> SystemSpec:
        data = _load(path)
        errors = self._validate(data)
        if errors:
            raise ParseError(errors)
        return self._build(data)

    def _validate(self, data: dict[str, Any]) -> list[ValidationError]:
        errors: list[ValidationError] = []

        # required top-level keys
        for key in ("system", "graph"):
            if key not in data:
                errors.append(ValidationError(key, f"Required field '{key}' is missing"))

        if "system" in data and not isinstance(data["system"], dict):
            errors.append(ValidationError("system", "Must be a mapping"))
        elif "system" in data and "name" not in data["system"]:
            errors.append(ValidationError("system.name", "Required field 'name' is missing"))

        if errors:
            return errors

        orchestrators: dict[str, Any] = data.get("orchestrators", {}) or {}
        agents: dict[str, Any] = data.get("agents", {}) or {}
        declared_ids = set(orchestrators) | set(agents)
        resolvers: dict[str, Any] = self._normalize_resolvers(data.get("resolvers", {}))
        tools: dict[str, Any] = data.get("tools", {}) or {}
        mcps: dict[str, Any] = data.get("mcps", {}) or {}

        self._validate_graph(data["graph"], declared_ids, errors)
        _validate_node_refs(orchestrators, agents, resolvers, tools, mcps, errors)
        _validate_mcps(mcps, errors)
        _validate_hooks(data.get("hooks"), errors)
        _validate_plugins(data.get("plugins"), errors)
        self._validate_no_secrets(data, errors)

        return errors

    def _validate_graph(
        self,
        graph: Any,
        declared_ids: set[str],
        errors: list[ValidationError],
        path: str = "graph",
        seen: list[str] | None = None,
    ) -> None:
        if not isinstance(graph, dict) or len(graph) == 0:
            errors.append(ValidationError(path, "Must be a non-empty mapping"))
            return
        if path == "graph" and len(graph) != 1:
            errors.append(ValidationError(path, f"Must have exactly one root, found {len(graph)}"))
            return

        seen = seen or []
        for node_id, children in graph.items():
            node_path = f"{path}.{node_id}"
            if node_id not in declared_ids:
                errors.append(
                    ValidationError(
                        node_path,
                        f"'{node_id}' is not declared in orchestrators or agents",
                    )
                )
            if node_id in seen:
                errors.append(
                    ValidationError(node_path, f"Cycle detected: {' -> '.join([*seen, node_id])}")
                )
                continue
            if children is not None:
                self._validate_graph(children, declared_ids, errors, node_path, [*seen, node_id])

    def _validate_no_secrets(
        self, data: object, errors: list[ValidationError], path: str = "$"
    ) -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                child = f"{path}.{key}"
                if _looks_secret(str(key)):
                    errors.append(
                        ValidationError(child, "Hardcoded secret-like key is not allowed")
                    )
                self._validate_no_secrets(value, errors, child)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._validate_no_secrets(item, errors, f"{path}[{i}]")
        elif isinstance(data, str) and _looks_secret(data):
            errors.append(ValidationError(path, "Hardcoded secret-like value is not allowed"))

    def _build(self, data: dict[str, Any]) -> SystemSpec:
        defaults = self._build_defaults(data.get("defaults"))
        resolvers = self._normalize_resolvers(data.get("resolvers", {}))
        tools: dict[str, Any] = data.get("tools", {}) or {}
        mcps: dict[str, Any] = data.get("mcps", {}) or {}
        orchestrators: dict[str, Any] = data.get("orchestrators", {}) or {}
        agents: dict[str, Any] = data.get("agents", {}) or {}

        node_specs = self._build_node_specs(orchestrators, agents, resolvers, tools, mcps, defaults)
        root_id, root_children = next(iter(data["graph"].items()))
        graph = self._build_graph_node(root_id, root_children, node_specs)

        return SystemSpec(
            meta=SystemMeta(name=data["system"]["name"]),
            defaults=defaults,
            graph=graph,
            hooks=_build_hooks(data.get("hooks")),
            plugins=_build_plugins(data.get("plugins")),
        )

    def _build_defaults(self, raw: Any) -> DefaultsConfig | None:
        if not isinstance(raw, dict):
            return None
        model_raw = raw.get("model")
        if not isinstance(model_raw, dict):
            return None
        return DefaultsConfig(model=self._build_model(model_raw))

    def _build_node_specs(
        self,
        orchestrators: dict[str, Any],
        agents: dict[str, Any],
        resolvers: dict[str, Any],
        tools: dict[str, Any],
        mcps: dict[str, Any],
        defaults: DefaultsConfig | None,
    ) -> dict[str, OrchestratorSpec | AgentSpec]:
        specs: dict[str, OrchestratorSpec | AgentSpec] = {}

        for node_id, raw in orchestrators.items():
            raw = raw or {}
            model = self._resolve_model(raw.get("model"), defaults)
            specs[node_id] = OrchestratorSpec(
                id=node_id,
                name=raw.get("name") or node_id,
                description=raw.get("description", ""),
                model=model,
                resolvers=self._build_resolvers(raw.get("resolvers", []), resolvers),
                protected=bool(raw.get("protected", False)),
                prompts=OrchestratorPromptSet(
                    orchestrator=raw.get("prompts", {}).get("orchestrator", ""),
                    system=raw.get("prompts", {}).get("system"),
                    user=raw.get("prompts", {}).get("user"),
                ),
            )

        for node_id, raw in agents.items():
            raw = raw or {}
            model = self._resolve_model(raw.get("model"), defaults)
            specs[node_id] = AgentSpec(
                id=node_id,
                name=raw.get("name") or node_id,
                description=raw.get("description", ""),
                model=model,
                resolvers=self._build_resolvers(raw.get("resolvers", []), resolvers),
                protected=bool(raw.get("protected", False)),
                prompts=BasePromptSet(
                    system=raw.get("prompts", {}).get("system") if raw.get("prompts") else None,
                    user=raw.get("prompts", {}).get("user") if raw.get("prompts") else None,
                ),
                tools=tuple(
                    ToolSpec(id=ref, description=tools[ref].get("description", ""))
                    for ref in raw.get("tools", [])
                ),
                mcps=tuple(_build_mcp_spec(ref, mcps[ref]) for ref in raw.get("mcps", [])),
            )

        return specs

    def _build_graph_node(
        self,
        node_id: str,
        children_raw: Any,
        node_specs: dict[str, OrchestratorSpec | AgentSpec],
    ) -> GraphNode:
        children: list[GraphNode] = []
        if isinstance(children_raw, dict):
            for child_id, grandchildren in children_raw.items():
                children.append(self._build_graph_node(child_id, grandchildren, node_specs))
        return GraphNode(node=node_specs[node_id], children=tuple(children))

    def _resolve_model(self, raw: Any, defaults: DefaultsConfig | None) -> ModelConfig:
        if isinstance(raw, dict):
            return self._build_model(raw)
        if defaults is not None:
            return defaults.model
        return ModelConfig(provider="", name="")

    def _build_model(self, raw: dict[str, Any]) -> ModelConfig:
        return ModelConfig(
            provider=raw.get("provider", ""),
            name=raw.get("name", ""),
            temperature=raw.get("temperature"),
        )

    def _build_resolvers(
        self, refs: list[str], resolvers: dict[str, Any]
    ) -> tuple[ResolverSpec, ...]:
        return tuple(
            ResolverSpec(id=ref, scope=resolvers.get(ref, {}).get("scope", "agent")) for ref in refs
        )

    @staticmethod
    def _normalize_resolvers(raw: Any) -> dict[str, Any]:
        if isinstance(raw, list):
            return {r: {"scope": "agent"} for r in raw}
        if isinstance(raw, dict):
            return {k: (v or {}) for k, v in raw.items()}
        return {}


def _looks_secret(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_MARKERS)


def _dedupe_stable(items: Iterable[str]) -> tuple[str, ...]:
    """Return items with duplicates removed, preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)
