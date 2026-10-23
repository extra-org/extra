"""Prepare an internal turn only after the client proves the sender's identity."""

from __future__ import annotations

import logging
from typing import NoReturn

from agent_manager.application.conversation_service import ConversationService
from agent_manager.application.prepared_conversation_turn import PreparedConversationTurn
from agent_manager.domain.channels.identity import (
    ChannelIdentityDenied,
    ChannelIdentityResolver,
    ChannelIdentityUnavailable,
)
from agent_manager.domain.channels.models import (
    ChannelClaimConflict,
    ChannelOwnerConflict,
    InboxItem,
    InboxState,
    bounded_identifier,
    stable_id,
)
from agent_manager.domain.channels.repository import ChannelRepository
from agent_manager.domain.identity import Principal

logger = logging.getLogger(__name__)


class ChannelTurnPreparer:
    def __init__(
        self,
        service: ConversationService,
        repository: ChannelRepository,
        identity_resolver: ChannelIdentityResolver,
        *,
        connection_id: str,
        system_id: str,
        organization_id: str,
    ) -> None:
        bounded_identifier(connection_id, limit=128)
        bounded_identifier(system_id, limit=256)
        bounded_identifier(organization_id)
        self._service = service
        self._repository = repository
        self._identities = identity_resolver
        self._connection_id = connection_id
        self._system_id = system_id
        self._organization_id = organization_id

    async def prepare(self, event_id: str, token: str) -> PreparedConversationTurn:
        item = await self._repository.get_inbound(event_id)
        if (
            item is None
            or not token
            or item.claim_token != token
            or item.state not in {InboxState.PREPARING, InboxState.PREPARED}
            or item.message.address.connection_id != self._connection_id
            or item.message.address.system_id != self._system_id
        ):
            raise ChannelClaimConflict("invalid channel preparation claim or scope")
        if item.message.kind != "text":
            raise ChannelClaimConflict("only text messages can prepare an agent turn")
        principal = await self.authorize(event_id, token)
        binding = await self._repository.get_binding(item.message.address)
        if binding is None:
            raise ChannelClaimConflict("channel message has no sender binding")
        conversation_id = binding.conversation_id or stable_id(
            "channel-conversation",
            self._connection_id,
            self._system_id,
            item.message.address.sender_id,
            str(binding.generation),
        )
        # A crash after create but before binding reuses this same id. Ownership
        # checks in ConversationService still reject another principal.
        await self._service.create(principal, session_id=conversation_id)
        try:
            await self._repository.bind_conversation(
                event_id, token, principal.user_id, self._organization_id, conversation_id
            )
        except ChannelOwnerConflict:
            await self._deny(item, token)
        turn = await self._service.prepare_turn(
            conversation_id,
            item.message.content,
            principal,
            idempotency_key=f"channel:{item.event_id}",
        )
        await self._repository.record_prepared(
            event_id, token, turn.session_id, turn.run_id, turn.message_id
        )
        return turn

    async def authorize(self, event_id: str, token: str) -> Principal:
        """Refresh the original sender's identity before execution or answer recovery."""
        item = await self._repository.get_inbound(event_id)
        if (
            item is None
            or not token
            or item.claim_token != token
            or item.state not in {InboxState.PREPARING, InboxState.PREPARED, InboxState.RUNNING}
            or item.message.address.connection_id != self._connection_id
            or item.message.address.system_id != self._system_id
        ):
            raise ChannelClaimConflict("invalid channel authorization claim or scope")
        principal = await self._principal(item, token)
        binding = await self._repository.get_binding(item.message.address)
        if binding is None or binding.active_event_id != event_id:
            raise ChannelClaimConflict("channel authorization requires the active sender claim")
        if (binding.user_id is not None and binding.user_id != principal.user_id) or (
            binding.organization_id is not None and binding.organization_id != self._organization_id
        ):
            await self._deny(item, token)
        if item.conversation_id is not None and binding.conversation_id != item.conversation_id:
            raise ChannelClaimConflict("channel conversation binding changed")
        return principal

    async def _principal(self, item: InboxItem, token: str) -> Principal:
        try:
            principal = await self._identities.resolve(item.message.address)
        except Exception as exc:
            logger.warning(
                "channel identity lookup failed: event=%s type=%s",
                item.event_id,
                type(exc).__name__,
            )
            await self._repository.finish(item.event_id, token, InboxState.NEEDS_REVIEW)
            raise ChannelIdentityUnavailable("channel identity could not be verified") from None
        if principal is not None and not isinstance(principal, Principal):
            await self._repository.finish(item.event_id, token, InboxState.NEEDS_REVIEW)
            raise ChannelIdentityUnavailable("identity resolver returned an invalid principal")
        if (
            principal is None
            or principal.is_anonymous
            or principal.organization_id != self._organization_id
        ):
            await self._deny(item, token)
        assert principal is not None
        return principal

    async def _deny(self, item: InboxItem, token: str) -> NoReturn:
        # A prepared manager message already exists and needs reconciliation;
        # it must not be silently abandoned as though it had never been made.
        state = InboxState.DENIED if item.state is InboxState.PREPARING else InboxState.NEEDS_REVIEW
        await self._repository.finish(item.event_id, token, state)
        raise ChannelIdentityDenied("sender has no authorized organization account link") from None
