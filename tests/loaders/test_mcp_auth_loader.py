"""Behaviour tests for MCPAuthLoader.

No real MCP server is touched: the returned httpx.Auth is driven directly
through its async_auth_flow against a throwaway httpx.Request, which is exactly
how httpx invokes it per request.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from agent_engine.loaders.mcp_auth_loader import MCPAuthLoader


def _write_auth_plugin(base_dir: Path, mcp_id: str, body: str) -> None:
    plugin_dir = base_dir / "plugins" / "mcp_auth"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / f"{mcp_id}.py").write_text(body, encoding="utf-8")


async def _headers_from(auth: httpx.Auth) -> dict[str, str]:
    request = httpx.Request("GET", "https://example.test/mcp")
    flow = auth.async_auth_flow(request)
    signed = await flow.__anext__()
    await flow.aclose()
    return dict(signed.headers)


def test_get_auth_returns_none_when_no_plugin(tmp_path: Path) -> None:
    assert MCPAuthLoader(tmp_path).get_auth("deepwiki") is None


def test_get_auth_returns_none_without_get_headers(tmp_path: Path) -> None:
    _write_auth_plugin(tmp_path, "deepwiki", "x = 1\n")
    assert MCPAuthLoader(tmp_path).get_auth("deepwiki") is None


async def test_auth_injects_headers_per_request(tmp_path: Path) -> None:
    _write_auth_plugin(
        tmp_path,
        "deepwiki",
        "async def get_headers() -> dict[str, str]:\n"
        "    return {'Authorization': 'Bearer abc'}\n",
    )
    auth = MCPAuthLoader(tmp_path).get_auth("deepwiki")
    assert auth is not None
    headers = await _headers_from(auth)
    assert headers["authorization"] == "Bearer abc"


async def test_get_headers_is_called_fresh_each_request(tmp_path: Path) -> None:
    # A counter file proves get_headers() runs on every request, not once.
    _write_auth_plugin(
        tmp_path,
        "deepwiki",
        "from pathlib import Path\n"
        f"_C = Path(r'{tmp_path}') / 'calls'\n"
        "async def get_headers() -> dict[str, str]:\n"
        "    n = int(_C.read_text()) + 1 if _C.exists() else 1\n"
        "    _C.write_text(str(n))\n"
        "    return {'X-Token': f'tok-{n}'}\n",
    )
    auth = MCPAuthLoader(tmp_path).get_auth("deepwiki")
    assert auth is not None
    first = await _headers_from(auth)
    second = await _headers_from(auth)
    assert first["x-token"] == "tok-1"
    assert second["x-token"] == "tok-2"


async def test_auth_plugin_can_import_shared(tmp_path: Path) -> None:
    resolvers = tmp_path / "plugins" / "resolvers"
    resolvers.mkdir(parents=True, exist_ok=True)
    (resolvers / "shared.py").write_text("TOKEN = 'from-shared'\n", encoding="utf-8")
    _write_auth_plugin(
        tmp_path,
        "deepwiki",
        "from shared import TOKEN\n"
        "async def get_headers() -> dict[str, str]:\n"
        "    return {'Authorization': f'Bearer {TOKEN}'}\n",
    )
    auth = MCPAuthLoader(tmp_path).get_auth("deepwiki")
    assert auth is not None
    headers = await _headers_from(auth)
    assert headers["authorization"] == "Bearer from-shared"
