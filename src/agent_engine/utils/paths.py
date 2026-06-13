"""Filesystem paths for an agent project directory.

An agent project is a directory that contains ``agents.yml``.  Everything else
— plugins, resolvers, prompts — lives at well-known sub-paths relative to that
directory.  ``ProjectPaths`` captures those conventions in one place so no
other module hard-codes path segments.

Usage::

    paths = ProjectPaths(base_dir)
    paths.tools_dir.mkdir(parents=True, exist_ok=True)
    stub = paths.tool("book_flight")       # Path
    rel  = ProjectPaths.tool_rel("book_flight")  # "plugins/tools/book_flight.py"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_PLUGINS = "plugins"
_TOOLS = "tools"
_RESOLVERS = "resolvers"
_RESOLVER_CONFIG = "resolvers.toml"
_RESOLVER_INIT = "__init__.py"
_RESOLVER_BASE = "base.py"
_ACCESS = "access.py"


@dataclass(frozen=True)
class ProjectPaths:
    """All well-known paths for a project rooted at ``base_dir``."""

    base_dir: Path

    # ------------------------------------------------------------------
    # Directories
    # ------------------------------------------------------------------

    @property
    def plugins_dir(self) -> Path:
        """``<base_dir>/plugins/``"""
        return self.base_dir / _PLUGINS

    @property
    def tools_dir(self) -> Path:
        """``<base_dir>/plugins/tools/``"""
        return self.plugins_dir / _TOOLS

    @property
    def resolvers_dir(self) -> Path:
        """``<base_dir>/plugins/resolvers/``"""
        return self.plugins_dir / _RESOLVERS

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def tool(self, tool_id: str) -> Path:
        """``<base_dir>/plugins/tools/{tool_id}.py``"""
        return self.tools_dir / f"{tool_id}.py"

    @property
    def resolver_config(self) -> Path:
        """``<base_dir>/plugins/resolvers/resolvers.toml``"""
        return self.resolvers_dir / _RESOLVER_CONFIG

    @property
    def resolver_init(self) -> Path:
        """``<base_dir>/plugins/resolvers/__init__.py``"""
        return self.resolvers_dir / _RESOLVER_INIT

    @property
    def resolver_base(self) -> Path:
        """``<base_dir>/plugins/resolvers/base.py``"""
        return self.resolvers_dir / _RESOLVER_BASE

    def resolver_agent(self, agent_id: str) -> Path:
        """``<base_dir>/plugins/resolvers/{agent_id}.py``"""
        return self.resolvers_dir / f"{agent_id}.py"

    @property
    def access_plugin(self) -> Path:
        """``<base_dir>/plugins/access.py``"""
        return self.plugins_dir / _ACCESS

    # ------------------------------------------------------------------
    # Display helpers (no base_dir needed)
    # ------------------------------------------------------------------

    @staticmethod
    def tool_rel(tool_id: str) -> str:
        """``plugins/tools/{tool_id}.py`` — for CLI output and error messages."""
        return f"{_PLUGINS}/{_TOOLS}/{tool_id}.py"

    @staticmethod
    def resolver_config_rel() -> str:
        """``plugins/resolvers/resolvers.toml`` — for CLI output and errors."""
        return f"{_PLUGINS}/{_RESOLVERS}/{_RESOLVER_CONFIG}"

    @staticmethod
    def resolver_init_rel() -> str:
        """``plugins/resolvers/__init__.py`` — for CLI output and errors."""
        return f"{_PLUGINS}/{_RESOLVERS}/{_RESOLVER_INIT}"

    @staticmethod
    def resolver_base_rel() -> str:
        """``plugins/resolvers/base.py`` — for CLI output and errors."""
        return f"{_PLUGINS}/{_RESOLVERS}/{_RESOLVER_BASE}"

    @staticmethod
    def resolver_agent_rel(agent_id: str) -> str:
        """``plugins/resolvers/{agent_id}.py`` — for CLI output and errors."""
        return f"{_PLUGINS}/{_RESOLVERS}/{agent_id}.py"
