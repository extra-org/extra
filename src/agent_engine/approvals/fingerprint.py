"""Stable, deterministic tool fingerprints.

A fingerprint is the cache key for a :class:`ToolContract`. It is derived only
from the tool *definition* (server identity + name + description + canonical
input schema + annotations) so that:

* the same definition always yields the same fingerprint (cache hit, no
  reclassification, stable decisions across restart/resume);
* any change to description, schema, or annotations changes the fingerprint
  (reclassification);
* two servers exposing the same tool name get different fingerprints.

Runtime argument values are never part of the fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_engine.approvals.contract import MCPServerIdentity, MCPToolDefinition


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, order-independent, stable separators.

    Missing fields are represented consistently (``None`` and ``{}`` are stable).
    Non-JSON values fall back to ``str`` so an exotic annotation cannot crash
    fingerprinting.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def tool_fingerprint(identity: MCPServerIdentity, tool: MCPToolDefinition) -> str:
    """Return the stable sha256 fingerprint for a discovered tool.

    Includes the server identity so identical tool names on different servers do
    not collide, and the full definition so any definition change invalidates the
    cache.
    """
    payload = {
        "server": identity.key,
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": dict(tool.input_schema or {}),
        "annotations": dict(tool.annotations or {}),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"tc_{digest}"
