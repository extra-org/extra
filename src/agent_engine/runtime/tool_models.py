from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolProviderName = Literal["local", "mcp", "unknown"]
ToolUsageStatus = Literal["started", "succeeded", "failed"]


@dataclass(frozen=True)
class ToolUsageRecord:
    """One runtime-observed tool call.

    Records intentionally omit arguments because tool inputs may contain
    sensitive or noisy user data.
    """

    name: str
    provider: ToolProviderName
    status: ToolUsageStatus
    agent_id: str | None = None
    server_id: str | None = None
    error: str | None = None
