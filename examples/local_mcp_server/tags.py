"""Pure request-parsing logic for the local demo MCP server.

No MCP / network imports here so this is trivially unit-testable. It covers the
two things the platform's MCP integration needs verified end-to-end:

1. tool-tag selection from the ``X-MCP-Tool-Tag`` header or a ``?tag=`` query
   param (the platform's default header transport and its query_param override);
2. a *safe* summary of auth/identity headers — presence only, never token values.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Default header the platform uses to send the tag selector.
DEFAULT_TAG_HEADER = "X-MCP-Tool-Tag"

#: Tool groups -> tool names. ``debug`` tools are always exposed so the
#: transport/auth can be verified regardless of the tag selector.
GROUPS: dict[str, tuple[str, ...]] = {
    "invoices": ("list_invoices", "get_invoice", "invoice_summary"),
    "customers": ("list_customers", "get_customer", "customer_summary"),
    "docs": ("search_docs", "get_doc"),
    "debug": ("echo", "server_info"),
}

_ALWAYS = "debug"


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def parse_tags(header_value: str | None, query_value: str | None = None) -> tuple[str, ...]:
    """Parse tags from the header and/or query value, comma-split + deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in [*_split(header_value), *_split(query_value)]:
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return tuple(out)


def select_tool_names(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Return the tool names to advertise for the given tags."""
    if not tags:
        return tuple(name for names in GROUPS.values() for name in names)

    selected: list[str] = []
    for group, names in GROUPS.items():
        if group == _ALWAYS or group in tags:
            selected.extend(names)
    return tuple(selected)


def _ci_get(headers: Mapping[str, str], name: str) -> str | None:
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return None


def auth_summary(headers: Mapping[str, str]) -> dict[str, object]:
    """Return *safe* auth/identity metadata — presence and scheme only."""
    authorization = _ci_get(headers, "Authorization")
    scheme = None
    if authorization:
        parts = authorization.split(" ", 1)
        scheme = parts[0] if parts[0] else None
    return {
        "authorization_present": authorization is not None,
        "auth_scheme": scheme,
        "organization_id": _ci_get(headers, "X-Organization-Id"),
        "correlation_id": _ci_get(headers, "X-Correlation-Id"),
        "tool_tag": _ci_get(headers, DEFAULT_TAG_HEADER),
    }
