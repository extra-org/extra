"""Execute one owned turn, or recover only its persisted answer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace
from typing import cast

from agent_engine.approvals.models import RunStatus
from agent_engine.runs.repository import RunRepository
from agent_engine.runtime.streaming import RunStreamEvent
from agent_manager.application.channels.controls import ChannelControlService
from agent_manager.application.channels.preparation import ChannelTurnPreparer
from agent_manager.application.channels.worker_options import ChannelWorkerOptions
from agent_manager.application.conversation_service import ConversationService
from agent_manager.application.errors import (
    ConversationAccessDenied,
    ConversationBranchConflict,
    ConversationTokenBudgetExceeded,
)
from agent_manager.application.prepared_conversation_turn import PreparedConversationTurn
from agent_manager.domain.channels.identity import ChannelIdentityDenied, ChannelIdentityUnavailable
from agent_manager.domain.channels.models import (
    ChannelClaimConflict,
    InboxItem,
    InboxState,
    bounded_text,
)
from agent_manager.domain.channels.repository import ChannelRepository

logger = logging.getLogger(__name__)
_ACTIVE = {InboxState.PREPARING, InboxState.PREPARED, InboxState.RUNNING}


class ChannelTurnProcessor:
    def __init__(
        self,
        service: ConversationService,
        repository: ChannelRepository,
        runs: RunRepository,
        preparer: ChannelTurnPreparer,
        options: ChannelWorkerOptions,
        *,
        controls: ChannelControlService | None = None,
    ) -> None:
        self._service = service
        self._repository = repository
        self._runs = runs
        self._preparer = preparer
        self._options = options
        self._controls = controls

    async def process(self, item: InboxItem) -> None:
        try:
            async with asyncio.timeout(self._options.turn_seconds):
                current = await self._repository.get_inbound(item.event_id)
                if (
                    current is None
                    or current.state not in _ACTIVE
                    or current.claim_token != item.claim_token
                    or not current.claim_token
                ):
                    return
                await self._process(current)
        except (ChannelIdentityDenied, ChannelIdentityUnavailable, ChannelClaimConflict):
            return
        except (
            ValueError,
            ConversationAccessDenied,
            ConversationBranchConflict,
            ConversationTokenBudgetExceeded,
        ):
            await self._review(item, "invalid_preparation")
        except TimeoutError:
            logger.warning("channel turn timed out: event=%s", item.event_id)
        except Exception as exc:
            # Claims remain durable. A later scan can retry preparation or
            # saved-answer staging; it can never repeat a running execution.
            logger.warning(
                "channel work failed: event=%s type=%s", item.event_id, type(exc).__name__
            )

    async def _process(self, item: InboxItem) -> None:
        assert item.claim_token is not None
        if self._controls is not None and await self._controls.handle(item):
            return
        if item.message.kind != "text":
            if item.state is InboxState.PREPARING:
                await self._repository.finish(item.event_id, item.claim_token, InboxState.FAILED)
            else:
                await self._review(item, "unsupported_work_state")
            return
        if item.state is InboxState.RUNNING:
            await self._stage_saved(item)
            return
        turn = await self._preparer.prepare(item.event_id, item.claim_token)
        principal = await self._preparer.authorize(item.event_id, item.claim_token)
        turn = replace(turn, principal=principal)
        response = await self._service.get_turn_response(turn.session_id, turn.run_id, principal)
        run = await self._runs.get(turn.run_id)
        if response is None and (run is None or run.status is not RunStatus.RUNNING):
            await self._review(item, "terminal_run_without_response")
            return
        if not await self._repository.claim_execution(item.event_id, item.claim_token):
            return
        current = await self._repository.get_inbound(item.event_id)
        assert current is not None
        if response is not None:
            await self._stage_saved(current)
            return
        approval = False
        cancellation_confirmed = False
        saw_final = False
        try:
            async with aclosing(
                cast(AsyncGenerator[RunStreamEvent, None], self._service.stream_turn(turn))
            ) as stream:
                async for event in stream:
                    if event.type == "pending_approval":
                        approval = True
                        cancellation_confirmed = await self._cancel_approval(
                            current, turn, event.approval_id
                        )
                        break
                    saw_final = saw_final or event.type == "final"
        except asyncio.CancelledError:
            await self._service.cancel_turn(turn)
            await self._review(current, "interrupted_execution")
            raise
        except Exception as exc:
            await self._service.fail_turn(turn)
            logger.warning(
                "channel execution failed: event=%s type=%s", item.event_id, type(exc).__name__
            )
            if approval:
                await self._review(current, "approval_stream_cleanup_failed")
                return
        if approval:
            # Cancellation alone is insufficient: keep the sender claimed until
            # the engine iterator has closed and awaited its owned tasks.
            if cancellation_confirmed:
                await self._repository.finish(current.event_id, item.claim_token, InboxState.FAILED)
            return
        if not saw_final:
            await self._service.fail_turn(turn)
        await self._stage_saved(current)

    async def _stage_saved(self, item: InboxItem) -> None:
        assert item.claim_token is not None
        principal = await self._preparer.authorize(item.event_id, item.claim_token)
        if item.conversation_id is None or item.run_id is None:
            await self._review(item, "missing_turn_binding")
            return
        response = await self._service.get_turn_response(
            item.conversation_id, item.run_id, principal
        )
        if response is None:
            await self._review(item, "execution_without_response")
            return
        if response.parent_message_id != item.message_id:
            await self._review(item, "response_binding_mismatch")
            return
        bounded_text(response.content)
        if not response.content.strip():
            await self._review(item, "empty_response")
            return
        await self._repository.stage_reply(
            item.event_id, item.claim_token, response.message_id, (response.content,)
        )

    async def _cancel_approval(
        self, item: InboxItem, turn: PreparedConversationTurn, approval_id: str | None
    ) -> bool:
        assert item.claim_token is not None
        if approval_id is None:
            await self._review(item, "missing_approval_binding")
            return False
        try:
            await self._service.cancel_pending_approval(
                turn.session_id, turn.run_id, approval_id, turn.principal
            )
            record = await self._runs.get(turn.run_id)
            if record is not None and record.status is RunStatus.CANCELLED:
                return True
        except Exception as exc:
            logger.warning(
                "channel approval cancellation failed: event=%s type=%s",
                item.event_id,
                type(exc).__name__,
            )
        await self._review(item, "unconfirmed_approval_cancellation")
        return False

    async def _review(self, item: InboxItem, reason: str) -> None:
        current = await self._repository.get_inbound(item.event_id)
        if (
            current is not None
            and current.state in _ACTIVE
            and current.claim_token == item.claim_token
            and current.claim_token
        ):
            await self._repository.finish(
                current.event_id, current.claim_token, InboxState.NEEDS_REVIEW
            )
            logger.warning(
                "channel work requires review: event=%s reason=%s", item.event_id, reason
            )
