"""One bounded outbound pump, owned by the connection's supervisor."""

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agent_manager.domain.channels.delivery import (
    ChannelSender,
    DeliveryPart,
    DeliveryRepository,
    SendOutcome,
)
from agent_manager.domain.channels.identity import ChannelIdentityResolver
from agent_manager.domain.identity import Principal

logger = logging.getLogger(__name__)


class ChannelDeliveryService:
    def __init__(
        self,
        repository: DeliveryRepository,
        identities: ChannelIdentityResolver,
        sender: ChannelSender,
        *,
        connection_id: str,
        system_id: str,
        organization_id: str,
        render: Callable[[str], tuple[str, ...]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = random.random,
        max_attempts: int = 4,
    ) -> None:
        if not 1 <= max_attempts <= 8:
            raise ValueError("send attempt bound must be between 1 and 8")
        self._repository, self._identities, self._sender = repository, identities, sender
        self._connection, self._system, self._organization = (
            connection_id,
            system_id,
            organization_id,
        )
        self._render, self._clock, self._jitter = render, clock, jitter
        self._max_attempts = max_attempts

    async def tick(self, stop: asyncio.Event) -> None:
        if stop.is_set():
            return
        # Only the exclusive supervisor calls tick, with no prior send in flight.
        await self._repository.recover_sending(self._connection, self._system)
        await self._repository.project(self._connection, self._system)
        for source in await self._repository.sources(self._connection, self._system):
            try:
                parts: list[str] = []
                for text in source.texts:
                    parts.extend(self._render(text))
                    if len(parts) > 128:
                        raise ValueError("reply has too many provider parts")
            except ValueError:
                parts = []
            await self._repository.materialize(source.event_id, tuple(parts))
        if stop.is_set():
            return
        part = await self._repository.claim_next(self._connection, self._system, self._clock())
        if part is None:
            return
        submitted = False
        try:
            guard = await self._repository.guard(part, self._clock())
            if guard is not None:
                await self._repository.complete(part, guard, reason=guard)
                return
            if part.user_id is not None and not await self._authorized(part):
                await self._repository.complete(
                    part, "blocked_identity", reason="identity_unavailable"
                )
                return
            # Identity lookup may await an external service. Recheck live state.
            guard = await self._repository.guard(part, self._clock())
            if guard is not None:
                await self._repository.complete(part, guard, reason=guard)
                return
            if stop.is_set():
                await self._repository.complete(
                    part, "retry", next_attempt_at=self._clock(), reason="stopped_before_send"
                )
                return
            submitted = True
            try:
                async with asyncio.timeout(20):
                    outcome = await self._sender.send(part.address.sender_id, part.text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("channel provider attempt failed: type=%s", type(exc).__name__)
                outcome = SendOutcome("unknown")
            if outcome.kind == "retry" and part.attempts < self._max_attempts:
                delay = min(
                    300, max(outcome.retry_after, 2**part.attempts * (0.5 + self._jitter()))
                )
                await self._repository.complete(
                    part,
                    "retry",
                    next_attempt_at=self._clock() + timedelta(seconds=delay),
                    reason="transient_rejection",
                )
            else:
                state = {
                    "accepted": "accepted",
                    "retry": "failed",
                    "failed": "failed",
                    "unknown": "delivery_unknown",
                }[outcome.kind]
                await self._repository.complete(
                    part,
                    state,
                    provider_id=outcome.provider_message_id,
                    reason=None if state == "accepted" else "send_" + state,
                )
        except asyncio.CancelledError:
            state = "delivery_unknown" if submitted else "retry"
            cleanup = asyncio.create_task(
                self._repository.complete(
                    part,
                    state,
                    next_attempt_at=None if submitted else self._clock(),
                    reason="interrupted_send" if submitted else "stopped_before_send",
                )
            )
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise

    async def _authorized(self, part: DeliveryPart) -> bool:
        try:
            async with asyncio.timeout(5):
                principal = await self._identities.resolve(part.address)
            return (
                isinstance(principal, Principal)
                and not principal.is_anonymous
                and principal.user_id == part.user_id
                and principal.organization_id == part.organization_id == self._organization
            )
        except Exception as exc:
            logger.warning("channel delivery identity unavailable: type=%s", type(exc).__name__)
            return False
