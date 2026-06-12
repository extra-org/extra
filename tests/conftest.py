"""Shared pytest fixtures.

The platform configures a single managed handler on the ``agentplatform``
package logger (see ``agentplatform.logging_setup``). Because that handler and
the logger's ``propagate``/level are process-global, a test that configures
logging could otherwise leak state into unrelated tests. This autouse fixture
snapshots and restores that state around every test so the suite stays
deterministic and quiet.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

import agentplatform.logging_setup as logging_setup


@pytest.fixture(autouse=True)
def _isolate_platform_logging() -> Iterator[None]:
    logger = logging.getLogger(logging_setup.PACKAGE_LOGGER_NAME)
    prev_handlers = logger.handlers[:]
    prev_propagate = logger.propagate
    prev_level = logger.level
    prev_managed = logging_setup._managed_handler

    try:
        yield
    finally:
        logger.handlers = prev_handlers
        logger.propagate = prev_propagate
        logger.setLevel(prev_level)
        logging_setup._managed_handler = prev_managed
