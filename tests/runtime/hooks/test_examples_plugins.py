"""The bundled examples keep all user extension code under one plugin package.

Proves the restructure into examples/plugins/{resolvers,tools,hooks}:
  * hooks load by full import path from examples.plugins.hooks;
  * the example hook YAML parses and validates;
  * the old examples/hooks path is gone and unreferenced;
  * resolvers and tools (file-path loaders) are unaffected.

These touch the real bundled example files — no network, no LLM. They rely on
the repo root being importable, which pytest guarantees (rootdir is on sys.path
and examples/ is a package).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_engine.core.validator import SystemSpecValidator
from agent_engine.loaders.resolver_loader import ResolverLoader
from agent_engine.loaders.tool_loader import ToolLoader
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks.loader import HookLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"
PLUGINS = EXAMPLES / "plugins"
HOOKS_DIR = PLUGINS / "hooks"
# The agent spec lives under examples/ (not inside the Python hook package).
HOOKS_YAML = EXAMPLES / "hooks_mcp_auth_agents.yml"


# -- hooks now live under the unified plugin package ------------------------


def test_old_examples_hooks_directory_is_gone() -> None:
    assert not (EXAMPLES / "hooks").exists()


def test_agent_spec_not_inside_python_hook_package() -> None:
    # App YAML must not live inside the importable Python package.
    assert HOOKS_YAML.is_file()
    assert not (HOOKS_DIR / "hooks_mcp_auth_agents.yml").exists()
    # The hook package holds only Python code (+ its __init__).
    assert not any(p.suffix in {".yml", ".yaml", ".toml"} for p in HOOKS_DIR.iterdir())


def test_hook_ref_loads_from_unified_plugin_package() -> None:
    fn = HookLoader().load(
        "before_mcp_request",
        "examples.plugins.hooks.mcp_auth:McpAuthHook.before_mcp_request",
        config={"credential_env": "INTERNAL_MCP_CREDENTIAL"},
    )
    assert callable(fn)
    assert fn.__module__ == "examples.plugins.hooks.mcp_auth"


def test_example_hook_yaml_uses_unified_package_path() -> None:
    text = HOOKS_YAML.read_text(encoding="utf-8")
    assert 'plugin: "mcp_auth"' in text
    assert "examples.plugins.hooks.mcp_auth:McpAuthHook." not in text
    # No bare module refs that depend on a directory being injected onto sys.path.
    assert 'ref: "mcp_auth:' not in text


def test_example_hook_yaml_parses_validates_and_all_refs_load() -> None:
    spec = YAMLParser().parse(str(HOOKS_YAML))
    errors = SystemSpecValidator().validate(spec, HOOKS_YAML.parent)
    assert errors == []

    assert spec.hooks.hooks  # non-empty
    for hook in spec.hooks.hooks:
        assert hook.plugin == "mcp_auth"
        assert hook.method
        assert hook.ref is None


def test_plugin_packages_are_importable() -> None:
    for pkg in (
        EXAMPLES,
        EXAMPLES / "plugins",
        EXAMPLES / "plugins" / "hooks",
        EXAMPLES / "plugins" / "resolvers",
        EXAMPLES / "plugins" / "tools",
    ):
        assert (pkg / "__init__.py").is_file(), f"missing __init__.py in {pkg}"


def test_per_type_toml_files_are_gone() -> None:
    # One unified manifest only — no hooks.toml / resolvers.toml / tools.toml.
    assert not (HOOKS_DIR / "hooks.toml").exists()
    assert not (PLUGINS / "resolvers" / "resolvers.toml").exists()
    assert not (PLUGINS / "tools" / "tools.toml").exists()


def test_unified_manifest_resolves_managed_hook_plugins() -> None:
    # Managed hook YAML uses logical plugin ids; the manifest maps those ids to
    # importable hook classes.
    assert (PLUGINS / "plugins.toml").is_file()
    spec = YAMLParser().parse(str(HOOKS_YAML))
    from agent_engine.runtime.hooks.manager import HookManager

    manager = HookManager.from_config(spec.hooks, manifest_path=PLUGINS / "plugins.toml")
    assert manager.has("before_mcp_request")


# -- resolvers and tools (file-path loaders) remain unaffected --------------


@pytest.fixture
def _isolate_shared_module() -> Iterator[None]:
    """ResolverLoader registers sys.modules['shared'] via setdefault, which is
    global. Snapshot and restore it so this test neither inherits a stale
    'shared' from another test nor leaks the examples one."""
    saved = sys.modules.pop("shared", None)
    try:
        yield
    finally:
        sys.modules.pop("shared", None)
        if saved is not None:
            sys.modules["shared"] = saved


def test_example_resolvers_still_load(_isolate_shared_module: None) -> None:
    # base_dir is the dir containing plugins/; ResolverLoader reads
    # plugins/resolvers/{agent_id}.py and instantiates its Resolver class.
    loader = ResolverLoader(EXAMPLES)
    # domestic_flights_agent's Resolver inherits SharedResolver — proves the
    # `from shared import SharedResolver` inheritance path still works.
    current_date = loader.load("domestic_flights_agent", "current_date")
    assert callable(current_date)
    assert isinstance(current_date({}), str)


def test_example_tools_still_load() -> None:
    book_flight = ToolLoader(EXAMPLES).load("book_flight")
    assert callable(book_flight)
    result = book_flight("TLV", "LHR", "2026-01-01")
    assert "Flight booked" in result
