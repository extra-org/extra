"""Behavior of the platform logging configuration and URL sanitizer."""

from __future__ import annotations

import logging

import pytest

from agentplatform.logging_setup import (
    PACKAGE_LOGGER_NAME,
    configure_logging,
    sanitize_url_for_logging,
)


def test_default_level_is_warning() -> None:
    configure_logging()
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.WARNING


def test_verbose_enables_info() -> None:
    configure_logging(verbose=True)
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.INFO


def test_debug_enables_debug() -> None:
    configure_logging(debug=True)
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.DEBUG


def test_quiet_only_errors() -> None:
    configure_logging(quiet=True)
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.ERROR


def test_debug_flag_wins_over_verbose() -> None:
    configure_logging(verbose=True, debug=True)
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.DEBUG


def test_configure_logging_is_idempotent_about_handlers() -> None:
    configure_logging(verbose=True)
    configure_logging(debug=True)
    configure_logging()
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    assert len(logger.handlers) == 1


def test_logs_go_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(verbose=True)
    logging.getLogger("agentplatform.example").info("hello-on-stderr")
    captured = capsys.readouterr()
    assert "hello-on-stderr" in captured.err
    assert "hello-on-stderr" not in captured.out


def test_default_suppresses_info_and_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    log = logging.getLogger("agentplatform.example")
    log.debug("debug-line")
    log.info("info-line")
    captured = capsys.readouterr()
    assert "debug-line" not in captured.err
    assert "info-line" not in captured.err


def test_debug_emits_debug_records(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(debug=True)
    logging.getLogger("agentplatform.example").debug("debug-visible")
    assert "debug-visible" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("https://user:s3cr3t@example.com/mcp", "s3cr3t"),
        ("https://example.com/mcp?token=abc123", "abc123"),
        ("https://example.com/mcp#frag-secret", "frag-secret"),
    ],
)
def test_sanitize_url_removes_secrets(url: str, secret: str) -> None:
    sanitized = sanitize_url_for_logging(url)
    assert secret not in sanitized
    assert "example.com" in sanitized


def test_sanitize_url_keeps_scheme_host_path() -> None:
    assert sanitize_url_for_logging("https://example.com/mcp") == "https://example.com/mcp"


def test_sanitize_url_preserves_port() -> None:
    assert sanitize_url_for_logging("http://example.com:8443/mcp") == "http://example.com:8443/mcp"


def test_sanitize_url_rejects_non_url() -> None:
    assert sanitize_url_for_logging("not a url") == "<non-url>"
