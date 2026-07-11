"""Checkpointer selection — one contract, chosen once at startup.

The engine depends on a single ``BaseCheckpointSaver`` abstraction regardless of
backend. This factory is the *only* place that branches on "postgres vs memory",
so no ``if postgres`` checks leak into the engine, nodes, or resume logic. After
construction, interrupt / checkpoint / resume behave identically for both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from agent_engine.logging_config import log

logger = logging.getLogger(__name__)


class CheckpointConfigError(RuntimeError):
    """A checkpoint connection string was supplied but its backend is unusable."""


@dataclass(frozen=True)
class CheckpointerHandle:
    """A checkpointer plus a flag describing whether it is durable/shared.

    ``persistent`` is False for the in-memory backend so the engine can emit the
    correct multi-pod warning and callers can reason about resume guarantees.
    """

    saver: BaseCheckpointSaver
    persistent: bool
    backend: str


class CheckpointProviderFactory:
    """Creates the checkpointer selected by configuration.

    * A connection string  -> a persistent, shared checkpointer (PostgreSQL).
    * No connection string  -> an in-memory checkpointer (process-local).
    """

    def create(self, connection_string: str | None) -> CheckpointerHandle:
        if connection_string:
            return self._create_persistent(connection_string)
        return self._create_memory()

    def _create_memory(self) -> CheckpointerHandle:
        log(
            logger,
            logging.WARNING,
            "in-memory checkpointer selected: state is process-local, is NOT shared "
            "between replicas/Pods, is lost on restart, and cannot resume a run on "
            "another instance. Set a checkpoint connection string for multi-replica "
            "production deployments.",
            backend="memory",
            persistent=False,
        )
        return CheckpointerHandle(saver=InMemorySaver(), persistent=False, backend="memory")

    def _create_persistent(self, connection_string: str) -> CheckpointerHandle:
        # Imported lazily so the engine has no hard dependency on the postgres
        # extra; a clear, typed error is raised only when it is actually needed.
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise CheckpointConfigError(
                "A checkpoint connection string was configured but the PostgreSQL "
                "checkpoint backend is not installed. Install "
                "'langgraph-checkpoint-postgres' (and 'psycopg') to enable shared, "
                "multi-Pod checkpointing."
            ) from exc

        saver = AsyncPostgresSaver.from_conn_string(connection_string)
        log(
            logger,
            logging.INFO,
            "persistent checkpointer selected: shared across replicas; runs resume "
            "by thread_id on any Pod.",
            backend="postgres",
            persistent=True,
        )
        return CheckpointerHandle(saver=saver, persistent=True, backend="postgres")
