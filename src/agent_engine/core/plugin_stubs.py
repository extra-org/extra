"""Startup detection of generated-but-unimplemented plugin functions.

``agentctl generate`` writes plugin stubs whose bodies are a single
``raise NotImplementedError``. A forgotten stub imports cleanly and only
explodes mid-request — the failure meets the user instead of the
developer. This module lets validation surface every unimplemented
plugin up front, in one consolidated report at startup.

Detection is purely static: plugin files are parsed (``ast``), never
executed, so scanning has no side effects. The scan is deliberately
conservative — it reports only what it can positively identify:

- a function/method whose body is just ``raise NotImplementedError``
  (optionally preceded by a docstring) is a stub;
- a missing plugin file for something the spec declares is an error;
- anything else (dynamic definitions, methods inherited from bases other
  than the conventional ``shared.py``, unparseable files) is skipped and
  left for the runtime loaders to diagnose.
"""

from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path

from agent_engine.core.errors import ValidationError
from agent_engine.core.spec import AgentSpec, GraphNode, SystemSpec

_GENERATE_HINT = "run `agentctl generate` to create the stub"


def scan_unimplemented_plugins(spec: SystemSpec, base_dir: Path) -> list[ValidationError]:
    """Return one error per declared-but-unimplemented plugin function."""
    scanner = _Scanner(base_dir)
    scanner.walk(spec.graph)
    if scanner.has_protected:
        scanner.check_access()
    return scanner.errors


class _Verdict(Enum):
    IMPLEMENTED = "implemented"
    STUB = "stub"
    NO_FILE = "no_file"
    NO_FUNCTION = "no_function"


class _Scanner:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._seen_tools: set[str] = set()
        self._seen_mcp_auth: set[str] = set()
        self.has_protected = False
        self.errors: list[ValidationError] = []

    def walk(self, node: GraphNode) -> None:
        if node.node.protected:
            self.has_protected = True
        if isinstance(node.node, AgentSpec):
            self._check_tools(node.node)
            self._check_resolvers(node.node)
            self._check_mcp_auth(node.node)
        for child in node.children:
            self.walk(child)

    # -- tools -----------------------------------------------------------

    def _check_tools(self, agent: AgentSpec) -> None:
        for tool in agent.tools:
            if tool.id in self._seen_tools:
                continue
            self._seen_tools.add(tool.id)
            rel = Path("plugins") / "tools" / f"{tool.id}.py"
            verdict = _stub_verdict(self._base_dir / rel, tool.id)
            if verdict is _Verdict.NO_FILE:
                self._error(
                    f"tools.{tool.id}",
                    f"Tool plugin not found: {rel} — {_GENERATE_HINT}",
                )
            elif verdict is _Verdict.STUB:
                self._error(
                    f"tools.{tool.id}",
                    f"Tool '{tool.id}' is declared but not implemented (generated stub): {rel}",
                )

    # -- resolvers -------------------------------------------------------

    def _check_resolvers(self, agent: AgentSpec) -> None:
        if not agent.resolvers:
            return
        agent_rel = Path("plugins") / "resolvers" / f"{agent.id}.py"
        agent_path = self._base_dir / agent_rel
        if not agent_path.is_file():
            self._error(
                f"{agent.id}.resolvers",
                f"Resolver plugin not found: {agent_rel} — {_GENERATE_HINT}",
            )
            return
        shared_path = self._base_dir / "plugins" / "resolvers" / "shared.py"
        for resolver in agent.resolvers:
            verdict = _stub_verdict(agent_path, resolver.id, class_name="Resolver")
            if verdict is _Verdict.NO_FUNCTION:
                # Fall back to the conventional shared base class.
                verdict = _stub_verdict(shared_path, resolver.id, class_name="SharedResolver")
            if verdict is _Verdict.STUB:
                self._error(
                    f"{agent.id}.resolvers.{resolver.id}",
                    f"Resolver '{resolver.id}' is declared but not implemented (generated stub)",
                )

    # -- MCP auth --------------------------------------------------------

    def _check_mcp_auth(self, agent: AgentSpec) -> None:
        for mcp in agent.mcps:
            if not mcp.auth or mcp.id in self._seen_mcp_auth:
                continue
            self._seen_mcp_auth.add(mcp.id)
            rel = Path("plugins") / "mcp_auth" / f"{mcp.id}.py"
            verdict = _stub_verdict(self._base_dir / rel, "get_headers")
            if verdict is _Verdict.NO_FILE:
                self._error(
                    f"mcps.{mcp.id}.auth",
                    f"MCP auth plugin not found: {rel} — {_GENERATE_HINT}",
                )
            elif verdict is _Verdict.STUB:
                self._error(
                    f"mcps.{mcp.id}.auth",
                    f"MCP auth 'get_headers' for '{mcp.id}' is not implemented (generated stub)",
                )

    # -- access ----------------------------------------------------------

    def check_access(self) -> None:
        # File existence for protected nodes is already validated by
        # SystemSpecValidator; only a positively identified stub is added here.
        path = self._base_dir / "plugins" / "access.py"
        verdict = _stub_verdict(path, "can_access", class_name="AccessResolver")
        if verdict is _Verdict.STUB:
            self._error(
                "plugins.access",
                "AccessResolver.can_access is not implemented (generated stub) — "
                "every protected node will be hidden for every caller",
            )

    def _error(self, field: str, message: str) -> None:
        self.errors.append(ValidationError(field, message))


# -- static stub detection ---------------------------------------------------


def _stub_verdict(path: Path, function_name: str, class_name: str | None = None) -> _Verdict:
    """Statically classify a plugin function without executing it.

    Files that fail to parse are treated as implemented — the runtime
    loader reports those with a better error.
    """
    if not path.is_file():
        return _Verdict.NO_FILE
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return _Verdict.IMPLEMENTED
    fn = _find_function(tree, function_name, class_name)
    if fn is None:
        return _Verdict.NO_FUNCTION
    return _Verdict.STUB if _is_stub_body(fn) else _Verdict.IMPLEMENTED


def _find_function(
    tree: ast.Module, name: str, class_name: str | None
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    body: list[ast.stmt] = tree.body
    if class_name is not None:
        cls = next(
            (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name),
            None,
        )
        if cls is None:
            return None
        body = cls.body
    return next(
        (
            n
            for n in body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        ),
        None,
    )


def _is_stub_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the body is just ``raise NotImplementedError`` (± docstring)."""
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    exc = body[0].exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
