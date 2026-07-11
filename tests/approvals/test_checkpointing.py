"""Checkpointer selection via the factory."""

from __future__ import annotations

import pytest

from agent_engine.engine.langgraph.checkpointing import (
    CheckpointConfigError,
    CheckpointProviderFactory,
)

try:  # optional extra; not installed in the default test environment
    import langgraph.checkpoint.postgres  # noqa: F401

    _HAS_POSTGRES = True
except ImportError:
    _HAS_POSTGRES = False


def test_no_connection_string_selects_in_memory() -> None:
    handle = CheckpointProviderFactory().create(None)
    assert handle.persistent is False
    assert handle.backend == "memory"
    assert handle.saver is not None


def test_empty_connection_string_selects_in_memory() -> None:
    handle = CheckpointProviderFactory().create("")
    assert handle.persistent is False


def test_memory_saver_satisfies_checkpointer_contract() -> None:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    handle = CheckpointProviderFactory().create(None)
    assert isinstance(handle.saver, BaseCheckpointSaver)


@pytest.mark.skipif(_HAS_POSTGRES, reason="postgres backend installed")
def test_connection_string_without_backend_raises_clear_error() -> None:
    with pytest.raises(CheckpointConfigError):
        CheckpointProviderFactory().create("postgresql://user:pass@localhost/db")
