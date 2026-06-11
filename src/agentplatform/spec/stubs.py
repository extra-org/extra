"""Stub generation for plugin files declared in the spec.

Derives the set of files that *should* exist from an ``AgentEngineSpec`` and
writes any that are missing.  Each tool and resolver declared in the YAML gets
its own dedicated stub file:

    plugins/tools/{tool_id}.py
    plugins/resolvers/{resolver_id}.py

Existing files are never overwritten — only missing stubs are added.  The CLI
``generate`` command is the entry point; this module owns the logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentplatform.spec.models import AgentEngineSpec
from agentplatform.utils import ProjectPaths


@dataclass(frozen=True)
class GenerateResult:
    created: list[str]


def generate_stubs(base_dir: Path, spec: AgentEngineSpec) -> GenerateResult:
    """Write missing stub files next to the YAML.

    Returns the list of files that were created.  Existing files are silently
    skipped — never overwritten.
    """
    paths = ProjectPaths(base_dir)
    created: list[str] = []

    # --- tools ---
    paths.tools_dir.mkdir(parents=True, exist_ok=True)
    for tool_id, tool_spec in spec.tools.items():
        stub = paths.tool(tool_id)
        if not stub.exists():
            stub.write_text(_tool_stub(tool_id, tool_spec.description), encoding="utf-8")
            created.append(ProjectPaths.tool_rel(tool_id))

    # --- resolvers ---
    paths.resolvers_dir.mkdir(parents=True, exist_ok=True)
    for resolver_id in spec.resolvers:
        stub = paths.resolver(resolver_id)
        if not stub.exists():
            stub.write_text(_resolver_stub(resolver_id), encoding="utf-8")
            created.append(ProjectPaths.resolver_rel(resolver_id))

    return GenerateResult(created=created)


def _tool_stub(tool_id: str, description: str) -> str:
    return (
        f'"""Tool: {tool_id}\n\n{description}\n"""\n'
        f"from __future__ import annotations\n\n\n"
        f"def {tool_id}() -> str:\n"
        f'    """{description}"""\n'
        f"    raise NotImplementedError\n"
    )


def _resolver_stub(resolver_id: str) -> str:
    return (
        f'"""Resolver: {resolver_id}\n\n'
        f"Returns a value injected as {{{{ {resolver_id} }}}} in prompt templates.\n"
        f'"""\n'
        f"from __future__ import annotations\n\n\n"
        f"def {resolver_id}() -> str:\n"
        f'    """Return the value for {{{{ {resolver_id} }}}}."""\n'
        f"    raise NotImplementedError\n"
    )
