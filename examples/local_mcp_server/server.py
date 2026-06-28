"""A local demo MCP server for end-to-end smoke testing.

Run it:

    python -m examples.local_mcp_server.server

It serves Streamable HTTP at ``http://127.0.0.1:8765/mcp`` by default.
Override with ``LOCAL_MCP_HOST``, ``LOCAL_MCP_PORT`` and ``LOCAL_MCP_PATH``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

from examples.local_mcp_server.tags import (
    GROUPS,
    auth_summary,
    parse_tags,
    select_tool_names,
)

logger = logging.getLogger("local_mcp_server")

SERVER_NAME = "local-mcp-demo"
SERVER_VERSION = "0.1.0"

_INVOICES = [
    {"id": "INV-001", "customer": "acme", "amount": 120.0, "status": "paid"},
    {"id": "INV-002", "customer": "globex", "amount": 80.5, "status": "open"},
    {"id": "INV-003", "customer": "acme", "amount": 240.0, "status": "open"},
]
_CUSTOMERS = [
    {"id": "acme", "name": "Acme Inc.", "tier": "gold"},
    {"id": "globex", "name": "Globex LLC", "tier": "silver"},
]
_DOCS = [
    {"id": "d1", "title": "Getting started", "body": "Install, configure, run."},
    {"id": "d2", "title": "Billing guide", "body": "Invoices are issued monthly."},
]


def _dump(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


_NO_ARGS: dict[str, object] = {"type": "object", "properties": {}, "additionalProperties": False}


def _str_arg(name: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {name: {"type": "string"}},
        "required": [name],
        "additionalProperties": False,
    }


def _list_invoices(_: dict[str, object]) -> str:
    return _dump(_INVOICES)


def _get_invoice(args: dict[str, object]) -> str:
    inv = next((i for i in _INVOICES if i["id"] == args.get("invoice_id")), None)
    return _dump(inv) if inv else _dump({"error": "invoice not found"})


def _invoice_summary(_: dict[str, object]) -> str:
    total = sum(cast(float, i["amount"]) for i in _INVOICES)
    open_count = sum(1 for i in _INVOICES if i["status"] == "open")
    return _dump({"count": len(_INVOICES), "total_amount": total, "open": open_count})


def _list_customers(_: dict[str, object]) -> str:
    return _dump(_CUSTOMERS)


def _get_customer(args: dict[str, object]) -> str:
    cust = next((c for c in _CUSTOMERS if c["id"] == args.get("customer_id")), None)
    return _dump(cust) if cust else _dump({"error": "customer not found"})


def _customer_summary(_: dict[str, object]) -> str:
    return _dump({"count": len(_CUSTOMERS)})


def _search_docs(args: dict[str, object]) -> str:
    q = str(args.get("query", "")).lower()
    hits = [{"id": d["id"], "title": d["title"]} for d in _DOCS if q in d["title"].lower()]
    return _dump(hits)


def _get_doc(args: dict[str, object]) -> str:
    doc = next((d for d in _DOCS if d["id"] == args.get("doc_id")), None)
    return _dump(doc) if doc else _dump({"error": "doc not found"})


def _echo(args: dict[str, object], headers: Mapping[str, str]) -> str:
    return _dump({"echo": args.get("text", ""), "received_auth": auth_summary(headers)})


def _server_info(_: dict[str, object], headers: Mapping[str, str]) -> str:
    return _dump(
        {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "groups": {g: list(names) for g, names in GROUPS.items()},
            "received_tags": list(parse_tags(cast(str | None, auth_summary(headers).get("tool_tag")))),
            "received_auth": auth_summary(headers),
        }
    )


_TOOLS: dict[str, tuple[str, dict[str, object], object]] = {
    "list_invoices": ("List all invoices.", _NO_ARGS, _list_invoices),
    "get_invoice": ("Get one invoice by id.", _str_arg("invoice_id"), _get_invoice),
    "invoice_summary": ("Totals across all invoices.", _NO_ARGS, _invoice_summary),
    "list_customers": ("List all customers.", _NO_ARGS, _list_customers),
    "get_customer": ("Get one customer by id.", _str_arg("customer_id"), _get_customer),
    "customer_summary": ("Count of customers.", _NO_ARGS, _customer_summary),
    "search_docs": ("Search docs by title substring.", _str_arg("query"), _search_docs),
    "get_doc": ("Get one doc by id.", _str_arg("doc_id"), _get_doc),
    "echo": ("Echo text and report received auth/tag metadata.", _str_arg("text"), _echo),
    "server_info": ("Report server info and received auth/tag metadata.", _NO_ARGS, _server_info),
}

_DEBUG_TOOLS = {"echo", "server_info"}


def _require_auth() -> bool:
    return os.getenv("LOCAL_MCP_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes")


def build_server() -> Server:
    server: Server = Server(SERVER_NAME)

    def _request() -> Request | None:
        try:
            ctx = server.request_context
        except LookupError:
            return None
        return getattr(ctx, "request", None)

    def _headers() -> Mapping[str, str]:
        req = _request()
        return dict(req.headers) if req is not None else {}

    def _query_tag() -> str | None:
        req = _request()
        return req.query_params.get("tag") if req is not None else None

    def _check_auth(headers: Mapping[str, str]) -> None:
        if _require_auth() and not auth_summary(headers)["authorization_present"]:
            raise ValueError("Authorization header required (LOCAL_MCP_REQUIRE_AUTH=true)")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        headers = _headers()
        _check_auth(headers)
        tags = parse_tags(cast(str | None, auth_summary(headers).get("tool_tag")), _query_tag())
        names = select_tool_names(tags)
        summary = auth_summary(headers)
        logger.info(
            "list_tools request: tags=%d tools=%d auth_present=%s scheme=%s org=%s",
            len(tags),
            len(names),
            summary["authorization_present"],
            summary["auth_scheme"],
            summary["organization_id"],
        )
        return [
            types.Tool(name=name, description=_TOOLS[name][0], inputSchema=_TOOLS[name][1])
            for name in names
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        headers = _headers()
        _check_auth(headers)
        if name not in _TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        logger.info("call_tool: %s", name)
        handler = _TOOLS[name][2]
        if name in _DEBUG_TOOLS:
            text = handler(arguments, headers)  # type: ignore[operator]
        else:
            text = handler(arguments)  # type: ignore[operator]
        return [types.TextContent(type="text", text=text)]

    return server


def build_app(server: Server | None = None) -> Starlette:
    server = server or build_server()
    session_manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)

    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    path = os.getenv("LOCAL_MCP_PATH", "/mcp")
    return Starlette(routes=[Mount(path, app=handle)], lifespan=lifespan)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = os.getenv("LOCAL_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("LOCAL_MCP_PORT", "8765"))
    path = os.getenv("LOCAL_MCP_PATH", "/mcp")
    logger.info("starting %s v%s on http://%s:%d%s", SERVER_NAME, SERVER_VERSION, host, port, path)
    logger.info("require_auth=%s", _require_auth())
    uvicorn.run(build_app(), host=host, port=port, log_level="warning", ws="none")


if __name__ == "__main__":
    main()
