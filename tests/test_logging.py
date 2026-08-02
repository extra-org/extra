from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel, ValidationError

from agent_engine.logging_config import (
    StructuredFormatter,
    configure_logging,
    current_request_id,
    log,
    request_id_var,
)
from agent_engine.observability.providers.logging import LoggingCallbackHandler


def test_configure_logging_respects_level():
    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING
    configure_logging("DEBUG")  # idempotent: reconfigures level, no duplicate handlers
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_reads_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    configure_logging()
    assert logging.getLogger().level == logging.ERROR


def test_configure_logging_no_duplicate_handlers():
    configure_logging("INFO")
    before = len(logging.getLogger().handlers)
    configure_logging("INFO")
    assert len(logging.getLogger().handlers) == before


def _format(event: str, fields: dict, request_id: str = "") -> str:
    fmt = StructuredFormatter("%(message)s")
    record = logging.LogRecord("n", logging.INFO, "f", 1, event, None, None)
    record.fields = fields
    token = request_id_var.set(request_id)
    try:
        return fmt.format(record)
    finally:
        request_id_var.reset(token)


def test_formatter_renders_fields_and_request_id():
    assert _format("tool start", {"name": "search"}, "abc123") == (
        "tool start request_id=abc123 name=search"
    )


def test_formatter_quotes_values_with_spaces():
    out = _format("request end", {"status": "error", "error": "boom went wrong"})
    assert "status=error" in out
    assert 'error="boom went wrong"' in out


def test_formatter_omits_request_id_when_unset():
    assert _format("system ready", {"agents": 2}) == "system ready agents=2"


def test_tool_event_emits_structured_record(caplog):
    token = request_id_var.set("abc123")
    try:
        assert current_request_id() == "abc123"
        with caplog.at_level(logging.INFO, logger="agent_engine.trace"):
            LoggingCallbackHandler().on_tool_start({"name": "search"}, "query")
    finally:
        request_id_var.reset(token)

    record = next(r for r in caplog.records if r.msg == "tool start")
    assert record.fields == {"name": "search"}


def test_log_helper_attaches_fields(caplog):
    with caplog.at_level(logging.INFO, logger="agent_engine.test"):
        log(logging.getLogger("agent_engine.test"), logging.INFO, "ping", n=1)
    record = caplog.records[-1]
    assert record.msg == "ping"
    assert record.fields == {"n": 1}


def test_preview_collapses_newlines():
    from agent_engine.api.app import _preview

    assert _preview("book\na\tflight") == "book a flight"  # no newline reaches the log


def test_begin_request_sanitizes_untrusted_id():
    from agent_engine.api.app import _begin_request

    # Injection chars stripped; a fully-bogus id falls back to a minted one.
    assert _begin_request("abc-123_ok") == "abc-123_ok"
    assert _begin_request("bad id\nINFO fake") == "badidINFOfake"
    minted = _begin_request("!@#$%")
    assert minted and " " not in minted


def test_log_helper_renders_exception_fields_without_payload(caplog):
    class _Args(BaseModel):
        message: str

    with pytest.raises(ValidationError) as excinfo:
        _Args(message={"secret": "hunter2"})  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="agent_engine.test"):
        log(logging.getLogger("agent_engine.test"), logging.WARNING, "boom", error=excinfo.value)

    rendered = caplog.records[-1].fields["error"]
    assert "hunter2" not in rendered  # the rejected input never reaches a log field
    assert "message:" in rendered  # but which field failed still does
    assert "\n" not in rendered
