"""Stable, collision-safe tool identity and session keys."""

from __future__ import annotations

from agent_engine.approvals.identity import tool_identity
from agent_engine.approvals.invocation import ToolInvocation


def test_local_tool_namespace() -> None:
    assert tool_identity(provider="local", server_id=None, tool_name="send") == "local:local:send"


def test_mcp_tool_scoped_by_server() -> None:
    a = tool_identity(provider="mcp", server_id="srvA", tool_name="send")
    b = tool_identity(provider="mcp", server_id="srvB", tool_name="send")
    # Same short name, different servers -> different identity (no shared approval).
    assert a != b
    assert a == "mcp:srvA:send"


def test_invocation_tool_identity_matches_helper() -> None:
    inv = ToolInvocation(
        tool_call_id="tc1",
        agent_id="a",
        tool_name="send",
        provider="mcp",
        server_id="srvA",
    )
    assert inv.tool_identity == "mcp:srvA:send"


def test_session_key_none_without_session_id() -> None:
    inv = ToolInvocation(tool_call_id="tc1", agent_id="a", tool_name="send")
    assert inv.session_key is None


def test_session_key_scoped_by_session_agent_and_tool() -> None:
    inv = ToolInvocation(tool_call_id="tc1", agent_id="a", tool_name="send", session_id="conv1")
    key = inv.session_key
    assert key is not None
    assert key.session_id == "conv1"
    assert key.agent_id == "a"
    assert key.tool_identity == "local:local:send"
