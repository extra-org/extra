"""Exact normalized controls and static feedback; never an LLM tool."""

from agent_manager.domain.channels.controls import ControlRepository
from agent_manager.domain.channels.models import InboxItem, InboxState, bounded_text
from agent_manager.domain.channels.repository import ChannelRepository


class ChannelControlService:
    def __init__(
        self,
        controls: ControlRepository,
        channels: ChannelRepository,
        *,
        support_contact: str | None,
    ) -> None:
        if support_contact is not None:
            bounded_text(support_contact)
            if not support_contact.strip() or len(support_contact.encode()) > 512:
                raise ValueError("a bounded support contact is required")
        self._controls, self._channels, self._support = controls, channels, support_contact

    async def handle(self, item: InboxItem) -> bool:
        suppressed = await self._controls.suppressed(item)
        if item.message.kind == "text" and not suppressed:
            return False
        if item.state is not InboxState.PREPARING:
            if (
                item.state in {InboxState.PREPARED, InboxState.RUNNING}
                and suppressed
                and item.claim_token
            ):
                await self._channels.finish(
                    item.event_id, item.claim_token, InboxState.NEEDS_REVIEW
                )
            return suppressed
        replies = {
            "reset": "נפתחה שיחה חדשה. אפשר לשלוח הודעת טקסט.",
            "help": f"לתמיכה אנושית: {self._support}" if self._support is not None else None,
            "start": "קבלת ההודעות חודשה. אפשר לשלוח הודעת טקסט.",
            "unsupported": "כרגע ניתן לקבל הודעות טקסט בלבד. אנא שלחו את הבקשה בטקסט.",
        }
        reply = (
            None if suppressed or item.message.kind == "stop" else replies.get(item.message.kind)
        )
        await self._controls.complete(item, reply, reset=item.message.kind == "reset")
        return True
