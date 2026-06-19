"""Tests for the unified plugins.toml manifest helper and generator wiring."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    HooksConfig,
    HookSpec,
    ModelConfig,
    ResolverSpec,
    SystemMeta,
    SystemSpec,
    ToolSpec,
)
from agent_engine.generate.generator import Generator
from agent_engine.generate.manifest import (
    MANIFEST_NAME,
    PluginManifestError,
    ensure_plugins_manifest_exists,
    hook_plugin_refs,
    manifest_package,
    update_manifest,
)

_MODEL = ModelConfig(provider="fake", name="fake", temperature=None)


def _read(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


# -- ensure_plugins_manifest_exists -----------------------------------------


def test_creates_manifest_and_init_when_missing(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    path, created = ensure_plugins_manifest_exists(root)

    assert created is True
    assert path == root / MANIFEST_NAME
    assert path.is_file()
    assert (root / "__init__.py").is_file()

    data = _read(path)
    assert data["package"]["name"]  # derived
    assert set(data["paths"]) == {"hooks", "resolvers", "tools"}
    assert set(data["hooks"]) == {
        "on_engine_start",
        "on_run_start",
        "before_mcp_request",
        "after_tool_call",
        "on_run_error",
        "plugins",
    }
    assert data["hooks"]["plugins"] == {}
    assert data["resolvers"] == {}
    assert data["tools"] == {}


def test_existing_manifest_is_not_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    path, _ = ensure_plugins_manifest_exists(root)
    update_manifest(path, tools={"book_flight": "pkg.tools.book_flight:book_flight"})
    before = path.read_text()

    path2, created = ensure_plugins_manifest_exists(root)

    assert path2 == path
    assert created is False
    assert path.read_text() == before  # untouched


def test_derives_package_from_path(tmp_path: Path) -> None:
    root = tmp_path / "examples" / "plugins"
    path, _ = ensure_plugins_manifest_exists(root)
    assert manifest_package(path) == "examples.plugins"


# -- update_manifest ---------------------------------------------------------


def test_update_adds_entries_to_each_section(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    changed = update_manifest(
        path,
        hooks={"before_mcp_request": ["pkg.hooks.auth:add"]},
        hook_plugins={"mcp_auth": "pkg.hooks.mcp_auth:McpAuthHook"},
        resolvers={"super_agent": "pkg.resolvers.super_agent:Resolver"},
        tools={"book_flight": "pkg.tools.book_flight:book_flight"},
    )
    assert changed is True

    data = _read(path)
    assert data["hooks"]["before_mcp_request"] == ["pkg.hooks.auth:add"]
    assert data["hooks"]["plugins"]["mcp_auth"] == "pkg.hooks.mcp_auth:McpAuthHook"
    assert data["resolvers"]["super_agent"] == "pkg.resolvers.super_agent:Resolver"
    assert data["tools"]["book_flight"] == "pkg.tools.book_flight:book_flight"


def test_update_preserves_existing_entry_without_force(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    update_manifest(path, tools={"book_flight": "user.custom:book_flight"})

    # A regeneration proposes a different ref for the same id — must be kept.
    changed = update_manifest(path, tools={"book_flight": "pkg.tools.book_flight:book_flight"})

    assert changed is False
    assert _read(path)["tools"]["book_flight"] == "user.custom:book_flight"


def test_force_overwrites_existing_entry(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    update_manifest(path, tools={"book_flight": "user.custom:book_flight"})

    changed = update_manifest(
        path, tools={"book_flight": "pkg.tools.book_flight:book_flight"}, force=True
    )

    assert changed is True
    assert _read(path)["tools"]["book_flight"] == "pkg.tools.book_flight:book_flight"


def test_hook_refs_are_deduped(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    update_manifest(path, hooks={"after_tool_call": ["pkg.hooks.audit:record"]})
    changed = update_manifest(path, hooks={"after_tool_call": ["pkg.hooks.audit:record"]})

    assert changed is False
    assert _read(path)["hooks"]["after_tool_call"] == ["pkg.hooks.audit:record"]


def test_update_is_idempotent(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    update_manifest(path, tools={"t": "pkg.tools.t:t"})
    snapshot = path.read_text()
    assert update_manifest(path, tools={"t": "pkg.tools.t:t"}) is False
    assert path.read_text() == snapshot


def test_secret_like_value_is_rejected(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    with pytest.raises(PluginManifestError):
        # A non-ref value containing a secret marker must be refused.
        update_manifest(path, tools={"bad": "Bearer my-secret-token value"})


def test_manifest_is_valid_toml(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    update_manifest(path, resolvers={"a": "pkg.resolvers.a:Resolver"})
    # Must round-trip through a strict TOML parser.
    assert _read(path)["resolvers"]["a"] == "pkg.resolvers.a:Resolver"


def test_hook_plugin_refs_loads_plugins_table(tmp_path: Path) -> None:
    path, _ = ensure_plugins_manifest_exists(tmp_path / "plugins")
    update_manifest(path, hook_plugins={"mcp_auth": "pkg.hooks.mcp_auth:McpAuthHook"})

    assert hook_plugin_refs(path) == {"mcp_auth": "pkg.hooks.mcp_auth:McpAuthHook"}


# -- Generator integration ---------------------------------------------------


def _spec_with_plugins() -> SystemSpec:
    agent = AgentSpec(
        id="flights",
        name="flights",
        description="books flights",
        model=_MODEL,
        prompts=BasePromptSet(),
        tools=(ToolSpec("book_flight", "book a flight"),),
        resolvers=(ResolverSpec("current_date", "shared"),),
    )
    return SystemSpec(
        meta=SystemMeta(name="gen-test"),
        defaults=None,
        graph=GraphNode(node=agent),
        hooks=HooksConfig(
            hooks=(
                HookSpec("after_tool_call", "pkg.hooks.audit:record"),
                HookSpec("before_mcp_request", plugin="mcp_auth", method="before_mcp_request"),
            )
        ),
    )


def test_generate_creates_manifest_with_refs(tmp_path: Path) -> None:
    result = Generator().generate(_spec_with_plugins(), tmp_path)

    manifest = tmp_path / "plugins" / MANIFEST_NAME
    assert manifest.is_file()
    assert MANIFEST_NAME in result.created

    data = _read(manifest)
    assert data["tools"]["book_flight"].endswith(".plugins.tools.book_flight:book_flight")
    assert data["resolvers"]["shared"].endswith(".plugins.resolvers.shared:SharedResolver")
    assert data["resolvers"]["flights"].endswith(".plugins.resolvers.flights:Resolver")
    assert data["hooks"]["after_tool_call"] == ["pkg.hooks.audit:record"]
    assert data["hooks"]["plugins"]["mcp_auth"].endswith(".plugins.hooks.mcp_auth:McpAuthHook")
    # Regression: resolver/tool stubs are still generated.
    assert (tmp_path / "plugins" / "tools" / "book_flight.py").is_file()
    assert (tmp_path / "plugins" / "resolvers" / "flights.py").is_file()
    hook_stub = tmp_path / "plugins" / "hooks" / "mcp_auth.py"
    assert hook_stub.is_file()
    assert "async def before_mcp_request(self, event: HookInvocation)" in hook_stub.read_text()


def test_generate_is_idempotent_and_preserves_manifest(tmp_path: Path) -> None:
    Generator().generate(_spec_with_plugins(), tmp_path)
    manifest = tmp_path / "plugins" / MANIFEST_NAME
    snapshot = manifest.read_text()

    result = Generator().generate(_spec_with_plugins(), tmp_path)

    assert manifest.read_text() == snapshot  # no churn
    assert MANIFEST_NAME in result.skipped  # existed, not recreated


def test_bundled_examples_manifest_is_consistent() -> None:
    # The committed examples/plugins/plugins.toml is valid and lists the package.
    manifest = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "plugins.toml"
    data = _read(manifest)
    assert data["package"]["name"] == "examples.plugins"
    assert data["tools"]["book_flight"] == "examples.plugins.tools.book_flight:book_flight"
    assert data["resolvers"]["shared"] == "examples.plugins.resolvers.shared:SharedResolver"
    assert data["hooks"]["before_mcp_request"] == []
    assert data["hooks"]["plugins"]["mcp_auth"] == "examples.plugins.hooks.mcp_auth:McpAuthHook"
