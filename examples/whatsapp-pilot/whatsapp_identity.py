"""Pilot identity: an administrator-managed allowlist of WhatsApp senders.

Possessing the phone number is the only proof. Use this for a closed pilot and
replace it with the organization's account-linking resolver before internal
information is exposed.
"""

import asyncio
import json
import os
from pathlib import Path

from agent_manager.domain.channels.identity import (
    ChannelIdentityResolver,
    ChannelIdentityUnavailable,
)
from agent_manager.domain.channels.models import ChannelAddress
from agent_manager.domain.identity import Principal


class AllowlistIdentityResolver(ChannelIdentityResolver):
    def __init__(self) -> None:
        self._path = Path(os.environ["WHATSAPP_PILOT_SENDERS_FILE"])
        self._organization_id = os.environ["EXTRA_WHATSAPP_ORGANIZATION_ID"]

    async def resolve(self, address: ChannelAddress) -> Principal | None:
        # Read on every lookup, so removing a sender revokes access immediately.
        try:
            text = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
            senders = json.loads(text)
        except (OSError, ValueError) as exc:
            raise ChannelIdentityUnavailable("sender allowlist is unreadable") from exc
        entry = senders.get(address.sender_id) if isinstance(senders, dict) else None
        if not isinstance(entry, dict):
            return None
        user_id, roles = entry.get("user_id"), entry.get("roles", [])
        display_name = entry.get("display_name")
        # A malformed entry denies access rather than granting partial roles.
        if (
            not isinstance(user_id, str)
            or not user_id.strip()
            or not isinstance(roles, list)
            or not all(isinstance(role, str) for role in roles)
            or not (display_name is None or isinstance(display_name, str))
        ):
            return None
        return Principal.external(
            user_id,
            display_name=display_name,
            roles=tuple(roles),
            organization_id=self._organization_id,
        )
