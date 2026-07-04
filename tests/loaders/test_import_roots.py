"""Tests for robust plugin import roots (CWD-independent package imports)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    HooksConfig,
    HookSpec,
    ModelConfig,
    PluginsConfig,
    SystemMeta,
    SystemSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.loaders.import_roots import (
    ImportRootError,
    register_import_roots,
    resolve_import_roots,
)
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks.errors import HookLoadError
from agent_engine.runtime.hooks.loader import HookLoader


@pytest.fixture
def _restore_sys() -> Iterator[None]:
    """Snapshot sys.path and sys.modules; restore after the test so global
    registration here cannot leak into other tests."""
    path_before = list(sys.path)
    mods_before = set(sys.modules)
    try:
        yield
    finally:
        sys.path[:] = path_before
        for name in set(sys.modules) - mods_before:
            sys.modules.pop(name, None)


def _make_pkg(root: Path, pkg: str) -> None:
    """Create an importable package `pkg` with a hook module under `root`."""
    pkg_dir = root / pkg
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "hooks.py").write_text("def my_hook(context):\n    return None\n", encoding="utf-8")


# -- resolution --------------------------------------------------------------


def test_resolve_is_relative_to_base_dir_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "proj" / "specs"
    base.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # CWD is intentionally not the project root

    assert resolve_import_roots(base, ["."]) == [base]
    assert resolve_import_roots(base, [".."]) == [base.parent]  # -> proj/


def test_missing_root_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ImportRootError) as exc:
        resolve_import_roots(tmp_path, ["does_not_exist"])
    msg = str(exc.value)
    assert "does_not_exist" in msg
    assert "not found" in msg


def test_duplicate_roots_are_deduped(tmp_path: Path) -> None:
    # "." and the absolute path point at the same directory -> one entry.
    resolved = resolve_import_roots(tmp_path, [".", str(tmp_path), "."])
    assert resolved == [tmp_path]


# -- registration ------------------------------------------------------------


def test_register_makes_package_importable_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_sys: None
) -> None:
    proj = tmp_path / "proj"
    _make_pkg(proj, "acme_plugins")
    base = proj / "specs"
    base.mkdir()
    monkeypatch.chdir(tmp_path)  # not the project root

    ref = "acme_plugins.hooks:my_hook"
    # Not importable before registration (the package is not on sys.path).
    with pytest.raises(HookLoadError):
        HookLoader().load("on_run_start", ref)

    roots = register_import_roots(base, [".."])  # base/.. == proj
    assert roots == [proj]

    fn = HookLoader().load("on_run_start", ref)  # now resolves
    assert callable(fn)


def test_empty_roots_leave_sys_path_unchanged(tmp_path: Path, _restore_sys: None) -> None:
    before = list(sys.path)
    assert register_import_roots(tmp_path, []) == []
    assert sys.path == before  # backwards compatible no-op


def test_register_is_idempotent(tmp_path: Path, _restore_sys: None) -> None:
    register_import_roots(tmp_path, ["."])
    count = sys.path.count(str(tmp_path.resolve()))
    register_import_roots(tmp_path, ["."])
    assert sys.path.count(str(tmp_path.resolve())) == count  # not added twice


# -- parser ------------------------------------------------------------------


def _write_spec(tmp_path: Path, plugins_block: str) -> SystemSpec:
    cfg = tmp_path / "agents.yml"
    cfg.write_text(
        "system:\n  name: t\n"
        "agents:\n  solo:\n    description: a\n"
        "graph:\n  solo:\n" + plugins_block,
        encoding="utf-8",
    )
    return YAMLParser().parse(str(cfg))


def test_parser_reads_import_roots(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path, 'plugins:\n  import_roots: ["..", "vendor"]\n')
    assert spec.plugins.import_roots == ("..", "vendor")


def test_parser_defaults_to_empty(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path, "")
    assert spec.plugins.import_roots == ()


def test_parser_rejects_non_list_roots(tmp_path: Path) -> None:
    from agent_engine.parsers.errors import ParseError

    with pytest.raises(ParseError) as exc:
        _write_spec(tmp_path, "plugins:\n  import_roots: '.'\n")
    assert "import_roots" in str(exc.value)


def test_parser_rejects_non_string_root(tmp_path: Path) -> None:
    from agent_engine.parsers.errors import ParseError

    with pytest.raises(ParseError) as exc:
        _write_spec(tmp_path, "plugins:\n  import_roots: [123]\n")
    assert "import_roots" in str(exc.value)


# -- engine integration ------------------------------------------------------


def _model_factory(provider: str, name: str, temperature: float | None) -> object:
    return object()  # build() never invokes the model for a tool-less agent


async def test_engine_build_registers_roots_before_loading_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_sys: None
) -> None:
    proj = tmp_path / "proj"
    _make_pkg(proj, "tenant_hooks")
    base = proj / "specs"
    base.mkdir()
    monkeypatch.chdir(tmp_path)  # launched outside the project root

    spec = SystemSpec(
        meta=SystemMeta(name="s"),
        defaults=None,
        graph=GraphNode(
            node=AgentSpec(
                id="solo",
                name="solo",
                description="d",
                model=ModelConfig(provider="fake", name="fake", temperature=None),
                prompts=BasePromptSet(),
            )
        ),
        # Hook is importable ONLY via the registered root, so a successful build
        # proves roots are registered before HookManager loads the refs.
        hooks=HooksConfig(hooks=(HookSpec("on_engine_start", "tenant_hooks.hooks:my_hook"),)),
        plugins=PluginsConfig(import_roots=("..",)),
    )

    engine = LangGraphEngine(base, model_factory=_model_factory)  # type: ignore[arg-type]
    await engine.build(spec)  # would raise HookLoadError if roots were not registered
    assert engine._hook_manager is not None


async def test_engine_build_without_roots_is_unchanged(tmp_path: Path, _restore_sys: None) -> None:
    # No import_roots and no hooks -> build proceeds, sys.path untouched.
    before = list(sys.path)
    spec = SystemSpec(
        meta=SystemMeta(name="s"),
        defaults=None,
        graph=GraphNode(
            node=AgentSpec(
                id="solo",
                name="solo",
                description="d",
                model=ModelConfig(provider="fake", name="fake", temperature=None),
                prompts=BasePromptSet(),
            )
        ),
    )
    engine = LangGraphEngine(tmp_path, model_factory=_model_factory)  # type: ignore[arg-type]
    await engine.build(spec)
    assert sys.path == before
