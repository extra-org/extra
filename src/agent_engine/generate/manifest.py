"""Unified plugin manifest (``plugins.toml``) for client extension code.

ONE manifest describes ALL client-provided extension code — hooks, resolvers,
and tools — for a single plugin package, replacing any per-type files (e.g. a
former ``hooks/hooks.toml``).

Role: it is mostly a **generation / documentation** artifact. The runtime reads
only ``[hooks.plugins]`` to resolve managed hook plugin ids to importable class
paths; explicit hook refs, resolvers, and tools keep their existing loaders.
``agentctl generate`` creates the manifest if missing and merges new entries
into it without clobbering manual edits.

Security: the manifest holds only import refs and package metadata — never
secrets. ``update_manifest`` rejects values that look like secret material.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from agent_engine.runtime.hooks.models import HOOK_POINTS

logger = logging.getLogger(__name__)

MANIFEST_NAME = "plugins.toml"

_SECRET_MARKERS = ("api_key", "apikey", "secret", "token", "password", "private_key")
_REF_LIKE = re.compile(r"^[A-Za-z0-9_.:]+$")  # import refs / dotted names / ids

_HEADER = """\
# plugins.toml — unified manifest for this client extension package.
#
# ONE manifest for ALL client extension code: hooks, resolvers, and tools.
# It is a catalog/generation companion. The runtime reads only [hooks.plugins]
# to resolve managed hook ids; resolvers and tools load by file path.
# `agentctl generate` creates this file if missing and merges new entries in
# without overwriting manual edits.
#
# SECURITY: never put secrets here. Only import refs and metadata — no tokens,
# client secrets, HMAC keys, or Authorization values.
"""


class PluginManifestError(RuntimeError):
    pass


def ensure_plugins_manifest_exists(
    plugin_root: Path, *, package: str | None = None
) -> tuple[Path, bool]:
    """Ensure ``plugin_root``, its ``__init__.py``, and ``plugins.toml`` exist.

    Returns ``(manifest_path, created)`` where ``created`` is True only when the
    manifest was newly written. An existing manifest is never overwritten.
    """
    plugin_root.mkdir(parents=True, exist_ok=True)
    init_file = plugin_root / "__init__.py"
    if not init_file.exists():
        init_file.write_text(
            '"""Client extension package (hooks, resolvers, tools)."""\n', encoding="utf-8"
        )
    path = plugin_root / MANIFEST_NAME
    if path.exists():
        return path, False
    pkg = package or _derive_package(plugin_root)
    path.write_text(_render(_default_manifest(pkg)), encoding="utf-8")
    logger.info("created plugin manifest %s", path)
    return path, True


def load_manifest(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def hook_plugin_refs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    plugins = load_manifest(path).get("hooks", {}).get("plugins", {})
    if not isinstance(plugins, dict):
        return {}
    return {str(key): str(value) for key, value in plugins.items()}


def manifest_package(path: Path) -> str:
    """Return the manifest's declared package name (or derive from the path)."""
    if path.exists():
        name = load_manifest(path).get("package", {}).get("name")
        if isinstance(name, str) and name:
            return name
    return _derive_package(path.parent)


def update_manifest(
    path: Path,
    *,
    hooks: dict[str, list[str]] | None = None,
    hook_plugins: dict[str, str] | None = None,
    resolvers: dict[str, str] | None = None,
    tools: dict[str, str] | None = None,
    package: str | None = None,
    force: bool = False,
) -> bool:
    """Merge entries into the manifest, preserving existing ones.

    - hooks: per-point lists are unioned (deduped), preserving order.
    - resolvers/tools: keyed tables; an existing key is preserved unless
      ``force`` is set. New keys are added.

    Returns True if the file changed. Rejects secret-like values.
    """
    data = load_manifest(path) if path.exists() else _default_manifest(package or "plugins")
    changed = False

    if hooks:
        section = data.setdefault("hooks", {})
        for point in HOOK_POINTS:
            section.setdefault(point, [])
        for hook_point, refs in hooks.items():
            existing = section.setdefault(hook_point, [])
            for ref in refs:
                if ref not in existing:
                    existing.append(ref)
                    changed = True

    if hook_plugins:
        section = data.setdefault("hooks", {})
        plugins = section.setdefault("plugins", {})
        for plugin_id, ref in hook_plugins.items():
            if plugin_id in plugins and not force:
                continue
            if plugins.get(plugin_id) != ref:
                plugins[plugin_id] = ref
                changed = True

    for name, entries in (("resolvers", resolvers), ("tools", tools)):
        if not entries:
            continue
        table = data.setdefault(name, {})
        for key, ref in entries.items():
            if key in table and not force:
                continue  # preserve the user's existing entry
            if table.get(key) != ref:
                table[key] = ref
                changed = True

    _assert_no_secrets(data)
    if changed:
        path.write_text(_render(data), encoding="utf-8")
    return changed


# -- internals --------------------------------------------------------------


def _derive_package(plugin_root: Path) -> str:
    parent, name = plugin_root.parent.name, plugin_root.name
    if name.isidentifier() and parent.isidentifier():
        return f"{parent}.{name}"
    return name if name.isidentifier() else "plugins"


def _default_manifest(pkg: str) -> dict:
    return {
        "package": {
            "name": pkg,
            "description": "Client extension package for hooks, resolvers, and tools.",
        },
        "paths": {
            "hooks": f"{pkg}.hooks",
            "resolvers": f"{pkg}.resolvers",
            "tools": f"{pkg}.tools",
        },
        "hooks": {**{point: [] for point in HOOK_POINTS}, "plugins": {}},
        "resolvers": {},
        "tools": {},
    }


def _assert_no_secrets(data: object) -> None:
    """Reject values that look like secret material (not refs/ids/dotted names)."""

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            normalized = value.lower().replace("-", "_")
            if any(m in normalized for m in _SECRET_MARKERS) and not _REF_LIKE.match(value):
                raise PluginManifestError(
                    "plugins.toml must not contain secrets; offending value rejected"
                )

    walk(data)


def _s(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _arr(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(_s(v) for v in values) + "]"


def _render(data: dict) -> str:
    lines: list[str] = [_HEADER.rstrip(), ""]

    pkg = data.get("package", {})
    lines.append("[package]")
    for key in ("name", "description"):
        if key in pkg:
            lines.append(f"{key} = {_s(str(pkg[key]))}")
    lines.append("")

    paths = data.get("paths", {})
    lines.append("[paths]")
    for key in ("hooks", "resolvers", "tools"):
        if key in paths:
            lines.append(f"{key} = {_s(str(paths[key]))}")
    lines.append("")

    hooks = data.get("hooks", {})
    lines.append("[hooks]")
    for point in HOOK_POINTS:
        lines.append(f"{point} = {_arr(list(hooks.get(point, [])))}")
    lines.append("")

    hook_plugins = hooks.get("plugins", {})
    lines.append("[hooks.plugins]")
    for key in sorted(hook_plugins):
        lines.append(f"{key} = {_s(str(hook_plugins[key]))}")
    lines.append("")

    for name in ("resolvers", "tools"):
        lines.append(f"[{name}]")
        table = data.get(name, {})
        for key in sorted(table):
            lines.append(f"{key} = {_s(str(table[key]))}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
