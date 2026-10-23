"""Client-owned identity lookup; transport authentication grants no privileges."""

from abc import ABC, abstractmethod

from agent_manager.domain.channels.models import ChannelAddress
from agent_manager.domain.identity import Principal


class ChannelIdentityDenied(PermissionError):
    """No authorized organization identity is linked to this sender."""


class ChannelIdentityUnavailable(RuntimeError):
    """Identity could not be verified; no privileged work may proceed."""


class ChannelIdentityResolver(ABC):
    async def close(self) -> None:  # noqa: B027 - optional, backwards-compatible cleanup
        """Release any client-owned lookup resources at application shutdown."""

    @abstractmethod
    async def resolve(self, address: ChannelAddress) -> Principal | None:
        """Resolve a current, revocable account link and its current permissions.

        Implemented by the client integration, never from message text. Return
        None for an unlinked/revoked sender. Credentials are request-scoped and
        must not be logged, cached on the resolver, or persisted in queue rows.
        """
