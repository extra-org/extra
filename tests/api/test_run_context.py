"""The engine's HTTP surface as a credential conduit: `Authorization` is
forwarded verbatim and verified nowhere, asserted facts are not accepted."""

from __future__ import annotations

from agent_engine.api.app import _run_context
from agent_engine.runtime.hooks.context import public_run_context

TOKEN = "eyJhbGciOiJIUzI1NiJ9.host-token.signature"


def test_a_bearer_credential_reaches_plugin_code() -> None:
    ctx = _run_context(None, run_id="r1", authorization=f"Bearer {TOKEN}")

    assert ctx.auth_context is not None
    assert ctx.auth_context.inbound_access_token == TOKEN


def test_the_scheme_is_matched_case_insensitively() -> None:
    ctx = _run_context(None, run_id="r1", authorization=f"bearer {TOKEN}")

    assert ctx.auth_context is not None
    assert ctx.auth_context.inbound_access_token == TOKEN


def test_a_caller_who_sends_nothing_gets_no_credential() -> None:
    """Never a fallback to some ambient key — that is the escalation removed."""
    for header in (None, "", "Bearer ", "Basic abc123"):
        ctx = _run_context(None, run_id="r1", authorization=header)

        assert ctx.auth_context is None, header


def test_the_credential_never_reaches_the_state_the_model_sees() -> None:
    """Graph state reaches prompts and callers; a credential there is a leak."""
    ctx = _run_context(None, run_id="r1", authorization=f"Bearer {TOKEN}")

    assert TOKEN not in repr(public_run_context(ctx))
