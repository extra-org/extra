"""Application-level composition: settings in, wired collaborators out."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agent_engine.approvals.in_memory_session_approval_repository import (
    InMemorySessionApprovalRepository,
)
from agent_engine.approvals.session_approval_repository import SessionApprovalRepository
from agent_engine.runs.in_memory import InMemoryRunRepository
from agent_engine.runs.repository import RunRepository
from agent_engine.tool_usage.in_memory import InMemoryToolUsageRepository
from agent_engine.tool_usage.repository import ToolUsageRepository
from agent_manager.application.channels.controls import ChannelControlService
from agent_manager.application.channels.delivery import ChannelDeliveryService
from agent_manager.application.channels.preparation import ChannelTurnPreparer
from agent_manager.application.channels.processing import ChannelTurnProcessor
from agent_manager.application.channels.worker import ChannelWorker
from agent_manager.application.channels.worker_options import ChannelWorkerOptions
from agent_manager.application.conversation_service import ConversationService
from agent_manager.config import AuthMode, Settings
from agent_manager.domain import Repository
from agent_manager.domain.channels.controls import ControlRepository
from agent_manager.domain.channels.delivery import DeliveryRepository
from agent_manager.domain.channels.identity import ChannelIdentityResolver
from agent_manager.domain.channels.repository import ChannelRepository
from agent_manager.domain.channels.status import ChannelStatusReader
from agent_manager.domain.channels.webhooks import WebhookRepository
from agent_manager.infrastructure.auth.anonymous_identity_source import AnonymousIdentitySource
from agent_manager.infrastructure.auth.claim_mapping import ClaimMapping
from agent_manager.infrastructure.auth.host_identity_source import HostIdentitySource
from agent_manager.infrastructure.auth.identity_resolver import IdentityResolver
from agent_manager.infrastructure.auth.identity_source import IdentitySource
from agent_manager.infrastructure.auth.static_secret_key_source import StaticSecretKeySource
from agent_manager.infrastructure.auth.token_policy import TokenPolicy
from agent_manager.infrastructure.auth.token_verifier import TokenVerifier
from agent_manager.infrastructure.channels.identity_loader import load_channel_identity_resolver
from agent_manager.infrastructure.channels.whatsapp.config import WhatsAppWebhookConfig
from agent_manager.infrastructure.channels.whatsapp.rendering import render_text
from agent_manager.infrastructure.channels.whatsapp.sender import WhatsAppSender
from agent_manager.infrastructure.channels.whatsapp.webhook import WhatsAppWebhook
from agent_manager.infrastructure.persistence.channels.controls import SqlControlRepository
from agent_manager.infrastructure.persistence.channels.delivery import SqlDeliveryRepository
from agent_manager.infrastructure.persistence.channels.ownership import SqlChannelWorkerOwnership
from agent_manager.infrastructure.persistence.channels.repository import SqlChannelRepository
from agent_manager.infrastructure.persistence.channels.status import SqlChannelStatusReader
from agent_manager.infrastructure.persistence.channels.webhooks import SqlWebhookRepository
from agent_manager.infrastructure.persistence.database import create_db_engine, session_factory
from agent_manager.infrastructure.persistence.memory_repository import MemoryRepository
from agent_manager.infrastructure.persistence.run_repository import SqlRunRepository
from agent_manager.infrastructure.persistence.sql_repository import SqlRepository

logger = logging.getLogger(__name__)

ANONYMOUS_KEY_DERIVATION_INFO = b"agent-chat-anonymous-pass"
EPHEMERAL_SECRET_BYTES = 32


@dataclass(frozen=True)
class ApplicationRepositories:
    conversations: Repository
    session_approvals: SessionApprovalRepository
    tool_usage: ToolUsageRepository
    runs: RunRepository
    channels: ChannelRepository | None = None
    webhooks: WebhookRepository | None = None
    deliveries: DeliveryRepository | None = None
    controls: ControlRepository | None = None
    status: ChannelStatusReader | None = None


def build_whatsapp_webhook(
    settings: Settings,
    repositories: ApplicationRepositories,
    system_id: str,
) -> WhatsAppWebhook | None:
    if not settings.extra_whatsapp_enabled:
        return None
    if repositories.webhooks is None:
        raise ValueError("WhatsApp intake requires a durable webhook repository")
    assert settings.extra_whatsapp_account_id is not None
    assert settings.extra_whatsapp_phone_number_id is not None
    assert settings.extra_whatsapp_app_secret is not None
    assert settings.extra_whatsapp_verify_token is not None
    return WhatsAppWebhook(
        WhatsAppWebhookConfig(
            connection_id=settings.extra_whatsapp_connection_id,
            system_id=system_id,
            account_id=settings.extra_whatsapp_account_id,
            phone_number_id=settings.extra_whatsapp_phone_number_id,
            app_secret=settings.extra_whatsapp_app_secret,
            verify_token=settings.extra_whatsapp_verify_token,
            max_body_bytes=settings.extra_whatsapp_max_body_bytes,
        ),
        repositories.webhooks,
    )


@asynccontextmanager
async def channel_worker_lifespan(
    settings: Settings,
    repositories: ApplicationRepositories,
    service: ConversationService,
    system_id: str,
) -> AsyncIterator[ChannelWorker | None]:
    if not settings.extra_whatsapp_worker_enabled:
        yield None
        return
    if repositories.channels is None:
        raise ValueError("channel worker requires durable storage")
    assert settings.extra_whatsapp_organization_id is not None
    assert settings.extra_whatsapp_identity_resolver is not None
    options = ChannelWorkerOptions(
        connection_id=settings.extra_whatsapp_connection_id,
        system_id=system_id,
        organization_id=settings.extra_whatsapp_organization_id,
        concurrency=settings.extra_whatsapp_worker_concurrency,
        poll_seconds=settings.extra_whatsapp_worker_poll_seconds,
        turn_seconds=settings.extra_whatsapp_worker_turn_seconds,
        drain_seconds=settings.extra_whatsapp_worker_drain_seconds,
        status_seconds=settings.extra_whatsapp_status_log_seconds,
    )
    identities = load_channel_identity_resolver(settings.extra_whatsapp_identity_resolver)
    sender: WhatsAppSender | None = None
    try:
        delivery = None
        controls = None
        if settings.extra_whatsapp_sender_enabled:
            assert repositories.deliveries is not None and repositories.controls is not None
            assert settings.extra_whatsapp_api_version is not None
            assert settings.extra_whatsapp_phone_number_id is not None
            assert settings.extra_whatsapp_access_token is not None
            assert settings.extra_whatsapp_support_contact is not None
            sender = WhatsAppSender(
                settings.extra_whatsapp_api_version,
                settings.extra_whatsapp_phone_number_id,
                settings.extra_whatsapp_access_token,
            )
            delivery = ChannelDeliveryService(
                repositories.deliveries,
                identities,
                sender,
                connection_id=options.connection_id,
                system_id=system_id,
                organization_id=options.organization_id,
                render=render_text,
            )
            controls = ChannelControlService(
                repositories.controls,
                repositories.channels,
                support_contact=settings.extra_whatsapp_support_contact,
            )
        preparer = ChannelTurnPreparer(
            service,
            repositories.channels,
            identities,
            connection_id=options.connection_id,
            system_id=system_id,
            organization_id=options.organization_id,
        )
        processor = ChannelTurnProcessor(
            service, repositories.channels, repositories.runs, preparer, options, controls=controls
        )
        ownership = SqlChannelWorkerOwnership(
            settings.effective_database_url, options.connection_id
        )
        async with ChannelWorker(
            repositories.channels,
            processor,
            ownership,
            options,
            delivery=delivery,
            status=repositories.status,
        ) as worker:
            yield worker
    finally:
        cleanup = asyncio.create_task(_close_channel_clients(sender, identities))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # A cancelled lifespan must still join client cleanup before its
            # repositories are disposed, including repeated caller cancellation.
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise


async def _close_channel_clients(
    sender: WhatsAppSender | None, identities: ChannelIdentityResolver
) -> None:
    try:
        if sender is not None:
            try:
                await sender.close()
            except Exception as exc:
                logger.warning("channel sender cleanup failed: type=%s", type(exc).__name__)
    finally:
        try:
            await identities.close()
        except Exception as exc:
            logger.warning("channel identity cleanup failed: type=%s", type(exc).__name__)


def build_session_approval_repository() -> SessionApprovalRepository:
    """Create the process-lifetime adapter used by the current application."""
    return InMemorySessionApprovalRepository()


def build_tool_usage_repository() -> ToolUsageRepository:
    """Select the tool-usage backend for this deployment.

    The only place the concrete adapter is named: a distributed deployment
    swaps the implementation here, and no agent, node, or tool-execution code
    changes with it.
    """
    return InMemoryToolUsageRepository()


def build_identity_resolver(settings: Settings) -> IdentityResolver:
    """Assemble the token sources a deployment's auth mode calls for."""
    return IdentityResolver(
        anonymous=AnonymousIdentitySource(
            anonymous_secret(settings),
            ttl_seconds=settings.extra_auth_anonymous_ttl_seconds,
        ),
        host=_build_host_identity_source(settings),
    )


def anonymous_secret(settings: Settings) -> str:
    """Derived from the host secret rather than reused, so a host that can mint
    tokens still cannot mint passes."""
    if settings.extra_auth_anonymous_secret:
        return settings.extra_auth_anonymous_secret
    if settings.extra_auth_secret:
        return hmac.new(
            settings.extra_auth_secret.encode(),
            ANONYMOUS_KEY_DERIVATION_INFO,
            hashlib.sha256,
        ).hexdigest()
    logger.warning(
        "No EXTRA_AUTH_SECRET or EXTRA_AUTH_ANONYMOUS_SECRET set;"
        " signing visitor passes with an ephemeral key that will not survive a restart."
    )
    return secrets.token_hex(EPHEMERAL_SECRET_BYTES)


def _build_host_identity_source(settings: Settings) -> IdentitySource | None:
    if settings.extra_auth_mode is AuthMode.ANONYMOUS:
        return None
    if not settings.extra_auth_secret:
        raise ValueError(f"EXTRA_AUTH_SECRET is required for auth mode {settings.extra_auth_mode}")

    mints_for_us = settings.extra_auth_mode is AuthMode.MINT
    verifier = TokenVerifier(
        StaticSecretKeySource(settings.extra_auth_secret),
        TokenPolicy(
            issuer=settings.extra_auth_issuer,
            audience=settings.extra_auth_audience,
            require_expiry=mints_for_us,
            max_ttl_seconds=settings.extra_auth_max_ttl_seconds if mints_for_us else None,
        ),
    )
    return HostIdentitySource(
        verifier,
        ClaimMapping(
            user_id=settings.extra_auth_claim_user_id,
            email=settings.extra_auth_claim_email,
            display_name=settings.extra_auth_claim_display_name,
            roles=settings.extra_auth_claim_roles,
            organization_id=settings.extra_auth_claim_organization_id,
        ),
    )


@asynccontextmanager
async def application_repositories(
    settings: Settings,
) -> AsyncIterator[ApplicationRepositories]:
    """Own application repositories for one complete process lifespan."""
    database_url = settings.effective_database_url
    session_approvals = build_session_approval_repository()
    tool_usage = build_tool_usage_repository()
    if settings.uses_process_memory:
        yield ApplicationRepositories(
            conversations=MemoryRepository(),
            session_approvals=session_approvals,
            tool_usage=tool_usage,
            runs=InMemoryRunRepository(),
        )
        return

    db_engine = create_db_engine(database_url)
    sessions = session_factory(db_engine)
    try:
        yield ApplicationRepositories(
            conversations=SqlRepository(sessions),
            session_approvals=session_approvals,
            tool_usage=tool_usage,
            runs=SqlRunRepository(sessions),
            channels=SqlChannelRepository(sessions),
            webhooks=SqlWebhookRepository(sessions),
            deliveries=SqlDeliveryRepository(sessions),
            controls=SqlControlRepository(sessions),
            status=SqlChannelStatusReader(sessions),
        )
    finally:
        await db_engine.dispose()
