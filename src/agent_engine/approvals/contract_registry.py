"""Persistence contract for tool contracts.

The execution/analysis layers depend only on the :class:`ToolContractRegistry`
interface (Dependency Inversion), never on a concrete store. The default
in-memory implementation is thread-safe and uses **create-if-absent** semantics:
concurrent classifications of the same fingerprint resolve deterministically to
the first stored contract, and duplicate saves are idempotent — so multiple
workers cannot produce inconsistent contracts for one fingerprint.

A production deployment can supply a shared, DB-backed implementation of the same
interface (e.g. an ``INSERT ... ON CONFLICT DO NOTHING`` keyed by fingerprint)
without touching callers.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from agent_engine.approvals.contract import ToolContract


class ToolContractRegistry(ABC):
    @abstractmethod
    def get(self, fingerprint: str) -> ToolContract | None: ...

    @abstractmethod
    def save(self, contract: ToolContract) -> ToolContract:
        """Persist ``contract`` if absent; return the authoritative stored contract.

        Idempotent and atomic create-if-absent: if a contract already exists for
        the fingerprint it is kept and returned unchanged, so racing writers agree
        on one contract.
        """


class InMemoryToolContractRegistry(ToolContractRegistry):
    """Process-local, thread-safe registry. Not shared across pods.

    Uses a plain lock for compare-and-set; the critical section is tiny (a dict
    lookup + insert), so contention is negligible.
    """

    def __init__(self) -> None:
        self._contracts: dict[str, ToolContract] = {}
        self._lock = threading.Lock()

    def get(self, fingerprint: str) -> ToolContract | None:
        with self._lock:
            return self._contracts.get(fingerprint)

    def save(self, contract: ToolContract) -> ToolContract:
        with self._lock:
            existing = self._contracts.get(contract.fingerprint)
            if existing is not None:
                return existing
            self._contracts[contract.fingerprint] = contract
            return contract
