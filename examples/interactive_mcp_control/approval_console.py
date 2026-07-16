from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from agent_engine.approvals.decision import ApprovalDecision
from agent_engine.approvals.sanitization import mask_arguments
from agent_engine.engine.types import PendingApproval

ReadLine = Callable[[str], str]


class ApprovalConsole:
    def __init__(self, read_line: ReadLine = input) -> None:
        self._read_line = read_line

    async def decide(self, pending: PendingApproval) -> ApprovalDecision:
        print("\n[LLM TOOL REQUEST]")
        print(f"server={pending.server_id or '-'}")
        print(f"tool={pending.tool_name}")
        print(f"approval_id={pending.approval_id}")
        print("\n[APPROVAL REQUIRED]")
        print(f"Agent: {pending.agent_id}")
        print(f"MCP server: {pending.server_id or '-'}")
        print(f"Tool: {pending.tool_name}")
        print("Arguments:")
        print(json.dumps(mask_arguments(pending.arguments), indent=2, default=str))
        print("Choose: 1=Allow once, 2=Allow for this session, 3=Deny")
        choices = {
            "1": ApprovalDecision.ALLOW_ONCE,
            "once": ApprovalDecision.ALLOW_ONCE,
            "2": ApprovalDecision.ALLOW_FOR_SESSION,
            "session": ApprovalDecision.ALLOW_FOR_SESSION,
            "3": ApprovalDecision.DENY,
            "deny": ApprovalDecision.DENY,
        }
        while True:
            raw = await asyncio.to_thread(self._read_line, "Decision: ")
            decision = choices.get(raw.strip().lower())
            if decision is not None:
                print(f"[DECISION] {decision.name}")
                return decision
            print("Invalid decision. Enter 1, 2, 3, once, session, or deny.")
