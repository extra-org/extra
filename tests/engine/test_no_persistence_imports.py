"""`agent_engine` must stay free of persistence concerns.

Persistence (SQLModel, SQLAlchemy, Alembic, DB sessions, `agent_manager`'s
repositories) belongs in the `agent_manager` application/service layer, never
in the stateless execution engine. This is a static, import-level guard: it
walks every `agent_engine` source file and asserts none of them import a
banned module, so a future change can't silently reintroduce the coupling.
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_ENGINE_ROOT = Path(__file__).resolve().parents[1].parent / "src" / "agent_engine"

BANNED_MODULE_PREFIXES = (
    "sqlmodel",
    "sqlalchemy",
    "alembic",
    "agent_manager",
)


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_banned(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in BANNED_MODULE_PREFIXES
    )


def test_agent_engine_source_files_exist() -> None:
    assert AGENT_ENGINE_ROOT.is_dir(), AGENT_ENGINE_ROOT


def test_agent_engine_never_imports_persistence_modules() -> None:
    violations: dict[str, set[str]] = {}
    for path in AGENT_ENGINE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        banned = {name for name in _imported_module_names(path) if _is_banned(name)}
        if banned:
            violations[str(path.relative_to(AGENT_ENGINE_ROOT))] = banned

    assert not violations, (
        "agent_engine must not import persistence/DB-framework or agent_manager "
        f"modules; found: {violations}"
    )
