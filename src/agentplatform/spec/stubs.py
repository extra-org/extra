"""Stub generation for plugin files declared in the spec.

Derives the set of files that *should* exist from an ``AgentEngineSpec`` and
writes any that are missing.  Tools still get one dedicated stub file each.
Resolvers are generated as one shared base class file plus one customer-owned
child class file per agent that declares resolvers:

    plugins/tools/{tool_id}.py
    plugins/resolvers/base.py
    plugins/resolvers/{agent_id}.py
    plugins/resolvers/resolvers.toml

Existing files are never overwritten — only missing stubs are added.  The CLI
``generate`` command is the entry point; this module owns the logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agentplatform.spec.models import AgentEngineSpec
from agentplatform.utils import ProjectPaths


class ResolverGenerateMode(StrEnum):
    ALL = "all"
    CHILDREN = "children"
    CHILD = "child"


@dataclass(frozen=True)
class GenerateResult:
    created: list[str]
    updated: list[str]
    stale: list[str]


def generate_stubs(
    base_dir: Path,
    spec: AgentEngineSpec,
    *,
    resolver_mode: ResolverGenerateMode = ResolverGenerateMode.ALL,
    resolver_agent_id: str | None = None,
    overwrite: bool = False,
) -> GenerateResult:
    """Write missing stub files next to the YAML.

    Returns the list of files that were created.  Existing files are silently
    skipped — never overwritten.
    """
    paths = ProjectPaths(base_dir)
    created: list[str] = []
    updated: list[str] = []
    stale: list[str] = []

    # --- tools ---
    paths.tools_dir.mkdir(parents=True, exist_ok=True)
    for tool_id, tool_spec in spec.tools.items():
        stub = paths.tool(tool_id)
        if not stub.exists():
            stub.write_text(_tool_stub(tool_id, tool_spec.description), encoding="utf-8")
            created.append(ProjectPaths.tool_rel(tool_id))

    # --- resolvers ---
    paths.resolvers_dir.mkdir(parents=True, exist_ok=True)
    resolver_agents = {
        agent_id: agent.resolvers for agent_id, agent in spec.agents.items() if agent.resolvers
    }
    shared_resolver_ids = {
        resolver_id
        for resolver_id, resolver in spec.resolvers.items()
        if resolver.scope == "shared"
    }
    for agent_id, agent_resolver_ids in resolver_agents.items():
        for resolver_id in agent_resolver_ids:
            if resolver_id not in spec.resolvers:
                raise ValueError(
                    f"Agent '{agent_id}' references resolver '{resolver_id}' "
                    "which is not defined in the resolvers section."
                )

    selected_agents = _select_resolver_agents(
        resolver_agents,
        mode=resolver_mode,
        agent_id=resolver_agent_id,
    )
    if selected_agents:
        _write_resolver_init(paths, created)
        if resolver_mode == ResolverGenerateMode.ALL:
            _write_resolver_base(
                paths,
                shared_resolver_ids,
                created,
                updated,
                overwrite=overwrite,
            )
        _write_agent_resolver_files(
            paths,
            selected_agents,
            shared_resolver_ids,
            created,
            updated,
            overwrite=overwrite,
        )
        _write_or_update_resolver_config(
            paths,
            selected_agents,
            created,
            updated,
            overwrite=overwrite and resolver_mode == ResolverGenerateMode.ALL,
        )
        stale.extend(_stale_resolver_items(paths, resolver_agents, shared_resolver_ids, spec))

    return GenerateResult(created=created, updated=updated, stale=stale)


def _tool_stub(tool_id: str, description: str) -> str:
    return (
        f'"""Tool: {tool_id}\n\n{description}\n"""\n'
        f"from __future__ import annotations\n\n\n"
        f"def {tool_id}() -> str:\n"
        f'    """{description}"""\n'
        f"    raise NotImplementedError\n"
    )


def _select_resolver_agents(
    resolver_agents: dict[str, list[str]],
    *,
    mode: ResolverGenerateMode,
    agent_id: str | None,
) -> dict[str, list[str]]:
    if mode == ResolverGenerateMode.CHILD:
        if agent_id is None:
            raise ValueError("resolver_agent_id is required when resolver_mode='child'.")
        if agent_id not in resolver_agents:
            raise ValueError(f"Agent '{agent_id}' does not declare resolvers.")
        return {agent_id: resolver_agents[agent_id]}
    if agent_id is not None:
        raise ValueError("resolver_agent_id can only be used when resolver_mode='child'.")
    return resolver_agents


def _write_resolver_init(paths: ProjectPaths, created: list[str]) -> None:
    if not paths.resolver_init.exists():
        paths.resolver_init.write_text('"""Resolver plugin package."""\n', encoding="utf-8")
        created.append(ProjectPaths.resolver_init_rel())


def _write_resolver_base(
    paths: ProjectPaths,
    shared_resolver_ids: set[str],
    created: list[str],
    updated: list[str],
    *,
    overwrite: bool,
) -> None:
    content = _resolver_base_stub(shared_resolver_ids)
    if not paths.resolver_base.exists():
        paths.resolver_base.write_text(content, encoding="utf-8")
        created.append(ProjectPaths.resolver_base_rel())
    elif overwrite:
        paths.resolver_base.write_text(content, encoding="utf-8")
        updated.append(ProjectPaths.resolver_base_rel())
    else:
        existing = paths.resolver_base.read_text(encoding="utf-8")
        missing_methods = [
            resolver_id
            for resolver_id in sorted(shared_resolver_ids)
            if f"def {resolver_id}(" not in existing
        ]
        if missing_methods:
            with paths.resolver_base.open("a", encoding="utf-8") as f:
                for resolver_id in missing_methods:
                    f.write(_shared_resolver_method_stub(resolver_id))
            updated.append(ProjectPaths.resolver_base_rel())


def _write_agent_resolver_files(
    paths: ProjectPaths,
    resolver_agents: dict[str, list[str]],
    shared_resolver_ids: set[str],
    created: list[str],
    updated: list[str],
    *,
    overwrite: bool,
) -> None:
    for agent_id, resolver_ids in resolver_agents.items():
        path = paths.resolver_agent(agent_id)
        rel = ProjectPaths.resolver_agent_rel(agent_id)
        agent_specific_resolvers = [
            resolver_id
            for resolver_id in dict.fromkeys(resolver_ids)
            if resolver_id not in shared_resolver_ids
        ]
        content = _agent_resolver_file_stub(agent_id, agent_specific_resolvers)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(rel)
        elif overwrite:
            path.write_text(content, encoding="utf-8")
            updated.append(rel)
        else:
            missing_methods = [
                resolver_id
                for resolver_id in agent_specific_resolvers
                if f"def {resolver_id}(" not in path.read_text(encoding="utf-8")
            ]
            if missing_methods:
                with path.open("a", encoding="utf-8") as f:
                    for resolver_id in missing_methods:
                        f.write(_resolver_method_stub(agent_id, resolver_id))
                updated.append(rel)


def _write_or_update_resolver_config(
    paths: ProjectPaths,
    resolver_agents: dict[str, list[str]],
    created: list[str],
    updated: list[str],
    *,
    overwrite: bool,
) -> None:
    content = _resolver_config_stub(resolver_agents)
    if not paths.resolver_config.exists():
        paths.resolver_config.write_text(content, encoding="utf-8")
        created.append(ProjectPaths.resolver_config_rel())
        return
    if overwrite:
        paths.resolver_config.write_text(content, encoding="utf-8")
        updated.append(ProjectPaths.resolver_config_rel())
        return

    existing = paths.resolver_config.read_text(encoding="utf-8")
    additions: list[str] = []
    if "base_class" not in existing:
        additions.append('\n[resolvers]\nbase_class = "plugins.resolvers.base.BaseResolver"\n')
    for agent_id in resolver_agents:
        table = f"[resolvers.agents.{agent_id}]"
        if table not in existing:
            additions.append(_resolver_config_agent_stub(agent_id))
    if additions:
        with paths.resolver_config.open("a", encoding="utf-8") as f:
            f.write("".join(additions))
        updated.append(ProjectPaths.resolver_config_rel())


def _stale_resolver_items(
    paths: ProjectPaths,
    resolver_agents: dict[str, list[str]],
    shared_resolver_ids: set[str],
    spec: AgentEngineSpec,
) -> list[str]:
    expected = {
        paths.resolver_init.name,
        paths.resolver_base.name,
        paths.resolver_config.name,
        *(f"{agent_id}.py" for agent_id in resolver_agents),
    }
    stale: list[str] = []
    for path in paths.resolvers_dir.glob("*.py"):
        if path.name not in expected:
            stale.append(f"plugins/resolvers/{path.name}")
    for agent_id in resolver_agents:
        path = paths.resolver_agent(agent_id)
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        for resolver_id in sorted(shared_resolver_ids):
            if f"def {resolver_id}(" in content:
                stale.append(
                    f"{ProjectPaths.resolver_agent_rel(agent_id)}::{resolver_id} "
                    "duplicates shared resolver"
                )
    if paths.resolver_base.exists():
        base_content = paths.resolver_base.read_text(encoding="utf-8")
        agent_scoped_ids = {
            resolver_id
            for resolver_id, resolver in spec.resolvers.items()
            if resolver.scope != "shared"
        }
        for resolver_id in sorted(agent_scoped_ids):
            if f"def {resolver_id}(" in base_content:
                stale.append(
                    f"{ProjectPaths.resolver_base_rel()}::{resolver_id} is no longer shared"
                )
        for method_name in _method_names(base_content):
            if method_name != "__init__" and method_name not in spec.resolvers:
                stale.append(
                    f"{ProjectPaths.resolver_base_rel()}::{method_name} is not declared in YAML"
                )
    return stale


def _resolver_base_stub(shared_resolver_ids: set[str]) -> str:
    imports = (
        "from agentplatform.runtime import ExecutionContext\n\n" if shared_resolver_ids else ""
    )
    methods = "".join(
        _shared_resolver_method_stub(resolver_id) for resolver_id in sorted(shared_resolver_ids)
    )
    return (
        '"""Shared base class for customer-owned resolver implementations."""\n\n'
        "from __future__ import annotations\n\n"
        "from abc import ABC\n\n"
        f"{imports}"
        "class BaseResolver(ABC):\n"
        '    """Base class for all generated agent resolver classes.\n\n'
        "    Shared dependencies such as REST clients, DB clients, auth clients,\n"
        "    or caches may be initialized here and reused by child resolver classes.\n"
        '    """\n\n'
        "    def __init__(self, rest_client: object | None = None) -> None:\n"
        "        self.rest_client = rest_client\n"
        f"{methods}"
    )


def _agent_resolver_file_stub(
    agent_id: str,
    resolver_ids: list[str],
) -> str:
    class_name = _resolver_class_name(agent_id)
    methods = "\n".join(
        _resolver_method_stub(agent_id, resolver_id) for resolver_id in dict.fromkeys(resolver_ids)
    )
    body = methods if methods else "\n    pass\n"
    return (
        f'"""Resolver implementation surface for {agent_id}."""\n\n'
        "from __future__ import annotations\n\n"
        "from agentplatform.runtime import ExecutionContext\n"
        "from plugins.resolvers.base import BaseResolver\n\n\n"
        f"class {class_name}(BaseResolver):\n"
        f'    """Resolver implementation surface for {agent_id}.\n\n'
        "    The customer must implement all methods declared in this class.\n"
        '    """\n'
        f"{body}"
    )


def _shared_resolver_method_stub(resolver_id: str) -> str:
    error = f"Customer must implement shared resolver {resolver_id}"
    return (
        "\n"
        f"    def {resolver_id}(self, ctx: ExecutionContext) -> object:\n"
        f'        """Return the shared value for {{{{ {resolver_id} }}}}."""\n'
        f'        raise NotImplementedError("{error}")\n'
    )


def _resolver_method_stub(agent_id: str, resolver_id: str) -> str:
    error = f"Customer must implement {resolver_id} for {agent_id}"
    return (
        "\n"
        f"    def {resolver_id}(self, ctx: ExecutionContext) -> object:\n"
        f'        """Return the value for {{{{ {resolver_id} }}}}."""\n'
        f'        raise NotImplementedError("{error}")\n'
    )


def _method_names(source: str) -> set[str]:
    return set(re.findall(r"^\s+def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", source, re.MULTILINE))


def _resolver_config_stub(resolver_agents: dict[str, list[str]]) -> str:
    agent_tables = "\n".join(_resolver_config_agent_stub(agent_id) for agent_id in resolver_agents)
    return (
        "# Resolver plugin configuration.\n"
        "# The runtime imports the base class and maps each selected agent id\n"
        "# to its customer-owned resolver implementation class.\n"
        "[resolvers]\n"
        'base_class = "plugins.resolvers.base.BaseResolver"\n\n'
        "# Optional customer-owned constructor dependencies. Values are passed\n"
        "# to resolver class __init__ methods as keyword arguments; replace them with your\n"
        "# own dependency handles or configuration names.\n"
        "[resolvers.dependencies]\n"
        'rest_client = "internal_rest_client"\n'
        f"{agent_tables}"
    )


def _resolver_config_agent_stub(agent_id: str) -> str:
    return (
        "\n"
        f"[resolvers.agents.{agent_id}]\n"
        f'class = "plugins.resolvers.{_resolver_module_name(agent_id)}.'
        f'{_resolver_class_name(agent_id)}"\n'
    )


def _resolver_class_name(agent_id: str) -> str:
    parts = [part for part in agent_id.replace("-", "_").split("_") if part]
    if not parts or not all(part.isidentifier() for part in parts):
        raise ValueError(f"Cannot generate resolver class name from agent id '{agent_id}'.")
    class_name = "".join(part[:1].upper() + part[1:] for part in parts) + "Resolver"
    if not class_name.isidentifier():
        raise ValueError(f"Cannot generate resolver class name from agent id '{agent_id}'.")
    return class_name


def _resolver_module_name(agent_id: str) -> str:
    if not agent_id.isidentifier():
        raise ValueError(f"Cannot generate resolver module name from agent id '{agent_id}'.")
    return agent_id
