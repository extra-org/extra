"""Logging configuration for the agent platform.

Lower-level modules only create module loggers (``logging.getLogger(__name__)``)
and emit records. The *single* place that decides log level, format, and output
stream is :func:`configure_logging`, which the CLI calls once at startup.

Design choices that keep this minimal and test-friendly:

- We configure the ``agentplatform`` package logger, not the root logger, so we
  never hijack logging for other libraries or pytest.
- ``propagate`` is disabled and a single managed handler is reused across calls,
  so repeated configuration (e.g. one CLI invocation per test) never stacks
  duplicate handlers.
- All records go to ``stderr``; the actual agent answer stays on ``stdout``.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

PACKAGE_LOGGER_NAME = "agent_engine"
LOG_FORMAT = "%(levelname)s %(name)s - %(message)s"

_managed_handler: logging.StreamHandler[TextIO] | None = None


def configure_logging(
    *,
    verbose: bool = False,
    debug: bool = False,
    quiet: bool = False,
) -> None:
    """Configure platform logging once, mapping CLI flags to a log level.

    Levels (most to least verbose flag wins):

    - ``debug``   → ``DEBUG``
    - ``verbose`` → ``INFO``
    - ``quiet``   → ``ERROR``
    - default     → ``WARNING``

    Idempotent: calling it again only updates the level and reuses the single
    managed stderr handler, so handlers never accumulate.
    """
    global _managed_handler

    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    elif quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if _managed_handler is None:
        _managed_handler = logging.StreamHandler(stream=sys.stderr)
        _managed_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(_managed_handler)
    else:
        # Re-resolve the stream so capture fixtures (capsys) that swap
        # ``sys.stderr`` after import still receive records.
        _managed_handler.setStream(sys.stderr)

    _managed_handler.setLevel(level)


def sanitize_url_for_logging(url: str) -> str:
    """Return a URL safe to log: scheme, host, port, and path only.

    Strips any ``user:password`` credentials, query string, and fragment, which
    are common carriers of tokens or secrets in MCP server URLs.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"

    if not parts.scheme and not parts.netloc:
        # Not a URL we can reason about; never echo it back verbatim.
        return "<non-url>"

    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
